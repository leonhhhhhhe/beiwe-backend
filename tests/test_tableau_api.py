from datetime import date, timedelta

import orjson
from django.http import StreamingHttpResponse

from authentication.tableau_authentication import (check_tableau_permissions,
    TableauAuthenticationFailed, TableauPermissionDenied)
from constants.tableau_api_constants import (SERIALIZABLE_FIELD_NAMES, X_ACCESS_KEY_ID,
    X_ACCESS_KEY_SECRET)
from database.security_models import ApiKey
from database.user_models_researcher import StudyRelation
from tests.common import ResearcherSessionTest, TableauAPITest
from tests.helpers import compare_dictionaries


class TestNewTableauAPIKey(ResearcherSessionTest):
    ENDPOINT_NAME = "admin_pages.new_tableau_api_key"
    
    def test_new_api_key(self):
        """ Asserts that:
            -one new api key is added to the database
            -that api key is linked to the logged in researcher
            -the correct readable name is associated with the key
            -no other api keys were created associated with that researcher
            -that api key is active and has tableau access  """
        self.assertEqual(ApiKey.objects.count(), 0)
        resp = self.smart_post(readable_name="test_generated_api_key")
        self.assertEqual(ApiKey.objects.count(), 1)
        api_key = ApiKey.objects.get(readable_name="test_generated_api_key")
        self.assertEqual(api_key.researcher.id, self.session_researcher.id)
        self.assertTrue(api_key.is_active)
        self.assertTrue(api_key.has_tableau_api_permissions)


class TestDisableTableauAPIKey(TableauAPITest):
    ENDPOINT_NAME = "admin_pages.disable_tableau_api_key"
    
    def test_disable_tableau_api_key(self):
        """ Asserts that:
            -exactly one fewer active api key is present in the database
            -the api key is no longer active """
        self.assertEqual(ApiKey.objects.filter(is_active=True).count(), 1)
        self.smart_post(api_key_id=self.api_key_public)
        self.assertEqual(ApiKey.objects.filter(is_active=True).count(), 0)
        self.assertFalse(ApiKey.objects.get(access_key_id=self.api_key_public).is_active)


class TestGetTableauDaily(TableauAPITest):
    ENDPOINT_NAME = "tableau_api.get_tableau_daily"
    today = date.today()
    yesterday = date.today() - timedelta(days=1)
    tomorrow = date.today() + timedelta(days=-1)
    # parameters are
    # end_date, start_date, limit, order_by, order_direction, participant_ids, fields
    
    # helpers
    @property
    def params_all_fields(self):
        return {"fields": ",".join(SERIALIZABLE_FIELD_NAMES)}
    
    @property
    def params_all_defaults(self):
        return {'participant_ids': self.default_participant.patient_id, **self.params_all_fields}
    
    @property
    def full_response_dict(self):
        defaults = self.default_summary_statistic_daily_cheatsheet()
        defaults["date"] = date.today().isoformat()
        defaults["participant_id"] = self.default_participant.patient_id
        defaults["study_id"] = self.session_study.object_id
        return defaults
    
    def smart_get_200_auto_headers(self, **kwargs) -> StreamingHttpResponse:
        return self.smart_get_status_code(
            200, self.session_study.object_id, data=kwargs, **self.raw_headers
        )
    
    def test_bad_field_name(self):
        self.generate_summary_statistic_daily()
        params = self.params_all_defaults
        params["fields"] = params["fields"].replace("accelerometer", "accellerometer")
        resp = self.smart_get_status_code(
            400, self.session_study.object_id, data=params, **self.raw_headers
        )
        self.assertEqual(
            resp.content, b'{"errors": ["beiwe_accellerometer_bytes is not a valid field"]}'
        )
    
    def test_summary_statistics_daily_no_params_empty_db(self):
        # unpack the raw headers like this, they magically just work because http language is weird
        resp = self.smart_get_200_auto_headers()
        response_content = b"".join(resp.streaming_content)
        self.assertEqual(response_content, b'[]')
    
    def test_summary_statistics_daily_all_params_empty_db(self):
        resp = self.smart_get_200_auto_headers(**self.params_all_fields)
        response_content = b"".join(resp.streaming_content)
        self.assertEqual(response_content, b'[]')
    
    def test_summary_statistics_daily_all_params_all_populated(self):
        self.generate_summary_statistic_daily()
        resp = self.smart_get_200_auto_headers(**self.params_all_defaults)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 1)
        assert compare_dictionaries(response_object[0], self.full_response_dict)
    
    def test_summary_statistics_daily_all_params_dates_all_populated(self):
        
        self.generate_summary_statistic_daily()
        params = {"end_date": date.today(), "start_date": date.today(), **self.params_all_defaults}
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 1)
        assert compare_dictionaries(response_object[0], self.full_response_dict)
    
    def test_summary_statistics_daily_all_fields_one_at_a_time(self):
        today = date.today()
        self.generate_summary_statistic_daily()
        cheat_sheet = self.default_summary_statistic_daily_cheatsheet()
        cheat_sheet["date"] = today.isoformat()
        cheat_sheet["participant_id"] = self.default_participant.patient_id
        cheat_sheet["study_id"] = self.session_study.object_id
        normal_params = self.params_all_defaults
        normal_params.pop("fields")
        for field in SERIALIZABLE_FIELD_NAMES:
            params = {"end_date": today, "start_date": today, "fields": field, **normal_params}
            resp = self.smart_get_200_auto_headers(**params)
            response_object = orjson.loads(b"".join(resp.streaming_content))
            self.assertEqual(len(response_object), 1)
            assert compare_dictionaries(response_object[0], {field: cheat_sheet[field]})
    
    def test_summary_statistics_daily_all_params_2_results_all_populated(self):
        self.generate_summary_statistic_daily()
        self.generate_summary_statistic_daily(a_date=self.yesterday)
        resp = self.smart_get_200_auto_headers(**self.params_all_defaults)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 2)
        compare_me = self.full_response_dict
        assert compare_dictionaries(response_object[0], compare_me)
        compare_me['date'] = self.yesterday.isoformat()
        assert compare_dictionaries(response_object[1], compare_me)
    
    def test_summary_statistics_daily_limit_param(self):
        self.generate_summary_statistic_daily()
        self.generate_summary_statistic_daily(a_date=self.yesterday)
        params = {"limit": 1, **self.params_all_defaults}
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 1)
        assert compare_dictionaries(response_object[0], self.full_response_dict)
    
    def test_summary_statistics_daily_date_ordering(self):
        self.generate_summary_statistic_daily()
        self.generate_summary_statistic_daily(a_date=self.yesterday)
        # the default ordering is ascending
        params = {"order_direction": "descending", **self.params_all_defaults}
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 2)
        compare_me = self.full_response_dict
        assert compare_dictionaries(response_object[0], compare_me)
        compare_me['date'] = self.yesterday.isoformat()  # set to yesterday
        assert compare_dictionaries(response_object[1], compare_me)
        
        # assert that ascending is correct
        params = {"order_direction": "ascending", **self.params_all_defaults}
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 2)
        assert compare_dictionaries(response_object[0], compare_me)
        compare_me['date'] = self.today.isoformat()  # revert to today
        assert compare_dictionaries(response_object[1], compare_me)
        
        # assert that empty ordering is the default
        params = {"order_direction": "", **self.params_all_defaults}
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 2)
        assert compare_dictionaries(response_object[0], compare_me)
        compare_me['date'] = self.yesterday.isoformat()  # set to yesterday
        assert compare_dictionaries(response_object[1], compare_me)
    
    def test_summary_statistics_daily_participant_ordering(self):
        self.generate_summary_statistic_daily()
        self.generate_summary_statistic_daily(participant=self.generate_participant(
            study=self.session_study, patient_id="22222222",
        ))
        # the default ordering is ascending
        params = {
            **self.params_all_defaults,
            # "order_direction": "ascending",
            "ordered_by": "participant_id",
            "participant_ids": self.default_participant.patient_id + ",22222222",
        }
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 2)
        compare_me = self.full_response_dict
        assert compare_dictionaries(response_object[1], compare_me)
        compare_me['participant_id'] = "22222222"  # set to particpant 2
        assert compare_dictionaries(response_object[0], compare_me)
        
        params["order_direction"] = "descending"
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(len(response_object), 2)
        assert compare_dictionaries(response_object[1], compare_me)
        compare_me['participant_id'] = self.default_participant.patient_id  # revert to participant 1
        assert compare_dictionaries(response_object[0], compare_me)
    
    def test_summary_statistics_daily_wrong_date(self):
        self.generate_summary_statistic_daily()
        params = self.params_all_defaults
        params["end_date"] = self.tomorrow
        params["start_date"] = self.tomorrow
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(response_object, [])
    
    def test_summary_statistics_daily_wrong_future_date(self):
        self.generate_summary_statistic_daily()
        params = self.params_all_defaults
        params["end_date"] = self.tomorrow
        params["start_date"] = self.tomorrow
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(response_object, [])
    
    def test_summary_statistics_daily_wrong_past_date(self):
        self.generate_summary_statistic_daily()
        params = self.params_all_defaults
        params["end_date"] = self.yesterday
        params["start_date"] = self.yesterday
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(response_object, [])
    
    def test_summary_statistics_daily_bad_participant(self):
        self.generate_summary_statistic_daily()
        params = self.params_all_defaults
        params["participant_ids"] = "bad_id"
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        self.assertEqual(response_object, [])
    
    def test_summary_statistics_daily_no_participant(self):
        self.generate_summary_statistic_daily()
        params = self.params_all_defaults
        params.pop("participant_ids")
        resp = self.smart_get_200_auto_headers(**params)
        response_object = orjson.loads(b"".join(resp.streaming_content))
        # self.assertEqual(response_object, [])
        assert compare_dictionaries(response_object[0], self.full_response_dict)


class TableauApiAuthTests(TableauAPITest):
    """ Test methods of the api authentication system """
    ENDPOINT_NAME = TableauAPITest.IGNORE_THIS_ENDPOINT
    
    def test_check_permissions_working(self):
        # if this doesn't raise an error in has succeeded
        check_tableau_permissions(self.default_header, study_object_id=self.session_study.object_id)
    
    def test_check_permissions_none(self):
        ApiKey.objects.all().delete()
        with self.assertRaises(TableauAuthenticationFailed) as cm:
            check_tableau_permissions(
                self.default_header, study_object_id=self.session_study.object_id
            )
    
    def test_check_permissions_inactive(self):
        self.api_key.update(is_active=False)
        with self.assertRaises(TableauAuthenticationFailed) as cm:
            check_tableau_permissions(
                self.default_header, study_object_id=self.session_study.object_id
            )
    
    def test_check_permissions_bad_secret(self):
        # note that ':' does not appear in base64 encoding, preventing any collision errors based on
        # the current implementation.
        class NotRequest:
            headers = {
                X_ACCESS_KEY_ID: self.api_key_public,
                X_ACCESS_KEY_SECRET: ":::" + self.api_key_private[3:],
            }
        with self.assertRaises(TableauAuthenticationFailed) as cm:
            check_tableau_permissions(
                NotRequest, study_object_id=self.session_study.object_id
            )
    
    def test_check_permissions_no_tableau(self):
        self.api_key.update(has_tableau_api_permissions=False)
        # ApiKey.objects.filter(access_key_id=self.api_key_public).update(
        #     has_tableau_api_permissions=False
        # )
        with self.assertRaises(TableauPermissionDenied) as cm:
            check_tableau_permissions(
                self.default_header, study_object_id=self.session_study.object_id
            )
    
    def test_check_permissions_bad_study(self):
        self.assertFalse(ApiKey.objects.filter(access_key_id=" bad study id ").exists())
        with self.assertRaises(TableauPermissionDenied) as cm:
            check_tableau_permissions(
                self.default_header, study_object_id=" bad study id "
            )
    
    def test_check_permissions_no_study_permission(self):
        StudyRelation.objects.filter(
            study=self.session_study, researcher=self.session_researcher).delete()
        with self.assertRaises(TableauPermissionDenied) as cm:
            check_tableau_permissions(
                self.default_header, study_object_id=self.session_study.object_id
            )
