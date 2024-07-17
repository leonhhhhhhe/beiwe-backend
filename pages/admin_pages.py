from datetime import datetime, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from markupsafe import Markup

from authentication.admin_authentication import authenticate_researcher_login
from config.settings import DOMAIN_NAME
from constants.message_strings import (API_KEY_IS_DISABLED, API_KEY_NOW_DISABLED, MFA_CODE_6_DIGITS,
    MFA_CODE_DIGITS_ONLY, MFA_CODE_MISSING, MFA_SELF_BAD_PASSWORD, MFA_SELF_DISABLED,
    MFA_SELF_NO_PASSWORD, MFA_SELF_SUCCESS, MFA_TEST_DISABLED, MFA_TEST_FAIL, MFA_TEST_SUCCESS,
    NEW_API_KEY_MESSAGE, NEW_PASSWORD_MISMATCH, NO_MATCHING_API_KEY, PASSWORD_RESET_SUCCESS,
    WRONG_CURRENT_PASSWORD)
from constants.security_constants import MFA_CREATED
from constants.user_constants import EXPIRY_NAME
from database.security_models import ApiKey
from database.user_models_researcher import Researcher
from forms.django_forms import DisableApiKeyForm, NewApiKeyForm
from libs.http_utils import easy_url
from libs.internal_types import ResearcherRequest
from libs.password_validation import check_password_requirements, get_min_password_requirement
from libs.security import create_mfa_object, qrcode_bas64_png, verify_mfa
from middleware.abort_middleware import abort


@authenticate_researcher_login
def self_manage_credentials_page(request: ResearcherRequest):
    """ The manage credentials page has two modes of access, one with a password and one without.
    When loaded with the password the MFA code's image is visible. There is also a special
    MFA_CREATED value in the session that forces the QR code to be visible without a password for
    one minute after it was created (based on page-load time). """
    
    # api key names for the researcher - these are sanitized by the template layer.
    api_keys = list(
        request.session_researcher.api_keys
        .filter(is_active=True)  # don't actually need is_active anymore, we are filtering on it.
        .order_by("-created_on").values(
        "access_key_id", "is_active", "readable_name", "created_on"
    ))
    for key in api_keys:
        key["created_on"] = key["created_on"].date().isoformat()
    
    password = request.POST.get("view_mfa_password", None)
    provided_password = password is not None
    password_correct = request.session_researcher.validate_password(password or "")
    has_mfa = request.session_researcher.mfa_token is not None
    mfa_created = request.session.get(MFA_CREATED, False)
    
    # check whether mfa_created occurred in the last 60 seconds, otherwise clear it.
    if isinstance(mfa_created, datetime) and (timezone.now() - mfa_created).total_seconds() > 60:
        del request.session[MFA_CREATED]
        mfa_created = False
    
    # mfa_created is a datetime which is non-falsey.
    if has_mfa and (mfa_created or password_correct):
        obj = create_mfa_object(request.session_researcher.mfa_token.strip("="))
        mfa_url = obj.provisioning_uri(name=request.session_researcher.username, issuer_name=DOMAIN_NAME)
        mfa_png = qrcode_bas64_png(mfa_url)
    else:
        mfa_png = None
    
    return render(
        request,
        'manage_credentials.html',
        context=dict(
            is_admin=request.session_researcher.is_an_admin(),
            api_keys=api_keys,
            new_api_access_key=request.session.pop("generate_api_key_id", None),
            new_api_secret_key=request.session.pop("new_api_secret_key", None),
            min_password_length=get_min_password_requirement(request.session_researcher),
            mfa_png=mfa_png,
            has_mfa=has_mfa,
            display_bad_password=provided_password and not password_correct,
            researcher=request.session_researcher,
        )
    )


@require_POST
@authenticate_researcher_login
def self_reset_mfa(request: ResearcherRequest):
    """ Endpoint either enables and creates a new, or clears the MFA toke for the researcher. 
    Sets a MFA_CREATED value in the session to force the QR code to be visible for one minute. """
    # requires a password to change the mfa setting, basic error checking.
    password = request.POST.get("mfa_password", None)
    if not password:
        messages.error(request, MFA_SELF_NO_PASSWORD)
        return redirect(easy_url("admin_pages.self_manage_credentials_page"))
    if not request.session_researcher.validate_password(password):
        messages.error(request, MFA_SELF_BAD_PASSWORD)
        return redirect(easy_url("admin_pages.self_manage_credentials_page"))
    
    # presence of a "disable" key in the post data to distinguish between setting and clearing.
    # manage adding to or removing MFA_CREATED from the session data.
    if "disable" in request.POST:
        messages.warning(request, MFA_SELF_DISABLED)
        if MFA_CREATED in request.session:
            del request.session[MFA_CREATED]
        request.session_researcher.clear_mfa()
    else:
        messages.warning(request, MFA_SELF_SUCCESS)
        request.session[MFA_CREATED] = timezone.now()
        request.session_researcher.reset_mfa()
    return redirect(easy_url("admin_pages.self_manage_credentials_page"))


@require_POST
@authenticate_researcher_login
def self_test_mfa(request: ResearcherRequest):
    """ endpoint to test your mfa code without accidentally locking yourself out. """
    if not request.session_researcher.mfa_token:
        messages.error(request, MFA_TEST_DISABLED)
        return redirect(easy_url("admin_pages.self_manage_credentials_page"))
    
    mfa_code = request.POST.get("mfa_code", None)
    if mfa_code and len(mfa_code) != 6:
        messages.error(request, MFA_CODE_6_DIGITS)
    if mfa_code and not mfa_code.isdecimal():
        messages.error(request, MFA_CODE_DIGITS_ONLY)
    if not mfa_code:
        messages.error(request, MFA_CODE_MISSING)
    
    # case: mfa is required, was provided, but was incorrect.
    if verify_mfa(request.session_researcher.mfa_token, mfa_code):
        messages.success(request, MFA_TEST_SUCCESS)
    else:
        messages.error(request, MFA_TEST_FAIL)
    
    return redirect(easy_url("admin_pages.self_manage_credentials_page"))


@require_POST
@authenticate_researcher_login
def self_change_password(request: ResearcherRequest):
    try:
        current_password = request.POST['current_password']
        new_password = request.POST['new_password']
        confirm_new_password = request.POST['confirm_new_password']
    except KeyError:
        return abort(400)
    
    if not Researcher.check_password(request.session_researcher.username, current_password):
        messages.warning(request, WRONG_CURRENT_PASSWORD)
        return redirect('admin_pages.self_manage_credentials_page')
    
    success, msg = check_password_requirements(request.session_researcher, new_password)
    if msg:
        messages.warning(request, msg)
    if not success:
        return redirect("admin_pages.self_manage_credentials_page")
    if new_password != confirm_new_password:
        messages.warning(request, NEW_PASSWORD_MISMATCH)
        return redirect('admin_pages.self_manage_credentials_page')
    
    # this is effectively sanitized by the hash operation
    request.session_researcher.set_password(new_password)
    request.session_researcher.update(password_force_reset=False)
    messages.warning(request, PASSWORD_RESET_SUCCESS)
    # expire the session so that the user has to log in again with the new password. (Ve have a
    # feature over in handle_session_expiry in admin_authentication that will block the session
    # period from being extended again if the timeout is within 10 seconds of expiring.)
    request.session[EXPIRY_NAME] = timezone.now() + timedelta(seconds=5)
    return redirect('admin_pages.self_manage_credentials_page')


@require_POST
@authenticate_researcher_login
def self_generate_api_key(request: ResearcherRequest):
    form = NewApiKeyForm(request.POST)
    if not form.is_valid():
        return redirect("admin_pages.self_manage_credentials_page")
    
    api_key = ApiKey.generate(
        researcher=request.session_researcher,
        readable_name=form.cleaned_data['readable_name'],
    )
    request.session["generate_api_key_id"] = api_key.access_key_id
    request.session["new_api_secret_key"] = api_key.access_key_secret_plaintext
    messages.warning(request, Markup(NEW_API_KEY_MESSAGE))
    return redirect("admin_pages.self_manage_credentials_page")


@require_POST
@authenticate_researcher_login
def self_disable_api_key(request: ResearcherRequest):
    form = DisableApiKeyForm(request.POST)
    if not form.is_valid():
        return redirect("admin_pages.self_manage_credentials_page")
    api_key_id = request.POST["api_key_id"]
    api_key_query = ApiKey.objects.filter(access_key_id=api_key_id) \
        .filter(researcher=request.session_researcher)
    
    if not api_key_query.exists():
        messages.warning(request, Markup(NO_MATCHING_API_KEY))
        return redirect("admin_pages.self_manage_credentials_page")
    
    api_key = api_key_query[0]
    if not api_key.is_active:
        messages.warning(request, API_KEY_IS_DISABLED + f" {api_key_id}")
        return redirect("admin_pages.self_manage_credentials_page")
    
    api_key.is_active = False
    api_key.save()
    messages.success(request, API_KEY_NOW_DISABLED.format(key=api_key.access_key_id))
    return redirect("admin_pages.self_manage_credentials_page")
