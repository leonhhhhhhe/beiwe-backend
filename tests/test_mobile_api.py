import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import time_machine
from dateutil import tz
from django.utils import timezone

from constants.common_constants import BEIWE_PROJECT_ROOT
from constants.message_strings import DEFAULT_HEARTBEAT_MESSAGE
from constants.schedule_constants import EMPTY_WEEKLY_SURVEY_TIMINGS
from constants.study_constants import (ABOUT_PAGE_TEXT, CONSENT_FORM_TEXT, DEFAULT_CONSENT_SECTIONS,
    SURVEY_SUBMIT_SUCCESS_TOAST_TEXT)
from constants.testing_constants import MIDNIGHT_EVERY_DAY, THURS_OCT_6_NOON_2022_NY
from database.data_access_models import FileToProcess
from database.schedule_models import AbsoluteSchedule, ScheduledEvent, WeeklySchedule
from database.system_models import GenericEvent
from database.user_models_participant import AppHeartbeats, AppVersionHistory
from libs.rsa import get_RSA_cipher
from libs.schedules import (get_start_and_end_of_java_timings_week,
    repopulate_absolute_survey_schedule_events, repopulate_relative_survey_schedule_events)
from libs.security import device_hash
from tests.common import ParticipantSessionTest


# trunk-ignore-all(ruff/B018)

#
## mobile_api
#


class TestParticipantSetPassword(ParticipantSessionTest):
    ENDPOINT_NAME = "mobile_api.set_password"
    
    def test_no_paramaters(self):
        self.smart_post_status_code(400)
        self.session_participant.refresh_from_db()
        self.assertFalse(
            self.session_participant.validate_password(self.DEFAULT_PARTICIPANT_PASSWORD)
        )
        self.assertTrue(
            self.session_participant.debug_validate_password(self.DEFAULT_PARTICIPANT_PASSWORD)
        )
    
    def test_correct_paramater(self):
        self.assertIsNone(self.default_participant.last_set_password)
        self.smart_post_status_code(200, new_password="jeff")
        self.session_participant.refresh_from_db()
        # participant passwords are weird there's some hashing
        self.assertFalse(self.session_participant.validate_password("jeff"))
        self.assertTrue(self.session_participant.debug_validate_password("jeff"))
        # test last_set_password_is_set
        self.assertIsInstance(self.default_participant.last_set_password, datetime)
    
    def test_deleted_participant(self):
        self.INJECT_DEVICE_TRACKER_PARAMS = False
        self.default_participant.update(deleted=True)
        response = self.smart_post_status_code(403)
        self.assertEqual(response.content, b"")
        self.INJECT_DEVICE_TRACKER_PARAMS = True


class TestGetLatestSurveys(ParticipantSessionTest):
    ENDPOINT_NAME = "mobile_api.get_latest_surveys"
    
    @property
    def BASIC_SURVEY_CONTENT(self):
        return [
            {
                '_id': self.DEFAULT_SURVEY_OBJECT_ID,
                'content': [],
                'settings': {},
                'survey_type': 'tracking_survey',
                'timings': EMPTY_WEEKLY_SURVEY_TIMINGS(),
                'name': "",
            }
        ]
    
    def test_no_surveys(self):
        resp = self.smart_post_status_code(200)
        self.assertEqual(resp.content, b"[]")
    
    def test_version_code_update_triggers_app_version_code_history(self):
        self.default_participant.update_only(last_version_code="1.0.0")
        self.assertFalse(AppVersionHistory.objects.exists())
        self.smart_post_status_code(200, version_code="1.0.1")
        self.default_participant.refresh_from_db()
        self.assertEqual(self.default_participant.last_version_code, "1.0.1")
        self.assertTrue(AppVersionHistory.objects.exists())
    
    def test_version_code_update_triggers_app_version_name_history(self):
        self.default_participant.update_only(last_version_name="1.0.0")
        self.assertFalse(AppVersionHistory.objects.exists())
        self.smart_post_status_code(200, version_name="1.0.1")
        self.default_participant.refresh_from_db()
        self.assertEqual(self.default_participant.last_version_name, "1.0.1")
        self.assertTrue(AppVersionHistory.objects.exists())
    
    def test_version_code_update_triggers_os_version_history(self):
        self.default_participant.update_only(last_os_version="1.0.0")
        self.assertFalse(AppVersionHistory.objects.exists())
        self.smart_post_status_code(200, os_version="1.0.1")
        self.default_participant.refresh_from_db()
        self.assertEqual(self.default_participant.last_os_version, "1.0.1")
        self.assertTrue(AppVersionHistory.objects.exists())
    
    def test_basic_survey(self):
        self.assertIsNone(self.default_participant.last_get_latest_surveys)
        self.default_survey
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        self.assertEqual(output_survey, self.BASIC_SURVEY_CONTENT)
        # test last_get_latest_surveys is set
        self.session_participant.refresh_from_db()
        self.assertIsInstance(self.default_participant.last_get_latest_surveys, datetime)
    
    def test_weekly_basics(self):
        self.default_survey
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        reference_output = self.BASIC_SURVEY_CONTENT
        reference_output[0]["timings"] = MIDNIGHT_EVERY_DAY()
        WeeklySchedule.create_weekly_schedules(MIDNIGHT_EVERY_DAY(), self.default_survey)
        self.assertEqual(output_survey, self.BASIC_SURVEY_CONTENT)
    
    def test_weekly_basics2(self):
        self.default_survey
        reference_output = self.BASIC_SURVEY_CONTENT
        reference_output[0]["timings"] = MIDNIGHT_EVERY_DAY()
        WeeklySchedule.create_weekly_schedules(MIDNIGHT_EVERY_DAY(), self.default_survey)
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        self.assertEqual(output_survey, reference_output)
    
    @time_machine.travel(THURS_OCT_6_NOON_2022_NY)
    def test_absolute_schedule_basics(self):
        # test for absolute surveys that they show up regardless of the day of the week they fall on,
        # as long as that day is within the current week.
        self.default_survey
        for day_of_week_index in self.iterate_weekday_absolute_schedules():
            resp = self.smart_post_status_code(200)
            api_survey_representation = json.loads(resp.content.decode())
            reference_representation = self.BASIC_SURVEY_CONTENT
            reference_representation[0]["timings"][day_of_week_index] = [0]
            self.assertEqual(api_survey_representation, reference_representation)
    
    def iterate_weekday_absolute_schedules(self):
        # iterates over days of the week and populates absolute schedules and scheduled events
        start, _ = get_start_and_end_of_java_timings_week(timezone.now())
        for i in range(0, 7):
            AbsoluteSchedule.objects.all().delete()
            ScheduledEvent.objects.all().delete()
            a_date = start.date() + timedelta(days=i)
            self.generate_absolute_schedule(a_date)
            repopulate_absolute_survey_schedule_events(
                self.default_survey, self.default_participant
            )
            # correct weekday for sunday-zero-index
            yield (a_date.weekday() + 1) % 7
    
    # absolutes
    def test_absolute_schedule_out_of_range_future(self):
        self.default_survey
        self.generate_absolute_schedule(date.today() + timedelta(days=200))
        repopulate_absolute_survey_schedule_events(self.default_survey, self.default_participant)
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        self.assertEqual(output_survey, self.BASIC_SURVEY_CONTENT)
    
    def test_absolute_schedule_out_of_range_past(self):
        self.default_survey
        self.generate_absolute_schedule(date.today() - timedelta(days=200))
        repopulate_absolute_survey_schedule_events(self.default_survey, self.default_participant)
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        self.assertEqual(output_survey, self.BASIC_SURVEY_CONTENT)
    
    @time_machine.travel(THURS_OCT_6_NOON_2022_NY)
    def test_relative_schedule_basics(self):
        # this test needds to run on a thursday
        # test that a relative survey creates schedules that get output in survey timings at all
        self.generate_relative_schedule(
            self.default_survey, self.default_intervention, days_after=-1
        )
        self.default_populated_intervention_date.date
        repopulate_relative_survey_schedule_events(self.default_survey, self.default_participant)
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        output_basic = self.BASIC_SURVEY_CONTENT
        timings_out = output_survey[0].pop("timings")
        timings_basic = output_basic[0].pop("timings")
        self.assertEqual(output_survey, output_basic)  # assert only the timings have changed
        self.assertNotEqual(timings_out, timings_basic)
        timings_basic[3].append(0)
        self.assertEqual(timings_out, timings_basic)
    
    def test_relative_schedule_out_of_range_future(self):
        self.generate_relative_schedule(
            self.default_survey, self.default_intervention, days_after=200
        )
        self.default_populated_intervention_date
        repopulate_relative_survey_schedule_events(self.default_survey, self.default_participant)
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        self.assertEqual(output_survey, self.BASIC_SURVEY_CONTENT)
    
    def test_relative_schedule_out_of_range_past(self):
        self.generate_relative_schedule(
            self.default_survey, self.default_intervention, days_after=-200
        )
        self.default_populated_intervention_date
        repopulate_relative_survey_schedule_events(self.default_survey, self.default_participant)
        resp = self.smart_post_status_code(200)
        output_survey = json.loads(resp.content.decode())
        self.assertEqual(output_survey, self.BASIC_SURVEY_CONTENT)
    
    # todo: work out how to iterate over variant relative schedules because that is obnoxious.
    # def test_something_relative(self):
    #     start, end = get_start_and_end_of_java_timings_week(timezone.now())
    
    #     for day in date_list(start, timedelta(days=1), 7):
    #         for self.iterate_days_relative_schedules(start, end, )
    
    def iterate_days_relative_schedules(self, days_before, days_after, date_of_intervention: date):
        # generates one relative schedule per day for the range given.
        # generates an intervention, and (possibly?) scheduled event for the schedule.
        # generates an intervention date on the default participant intervention date
        intervention = self.generate_intervention(self.default_study, "an intervention")
        self.generate_intervention_date(
            self.default_participant, intervention, date_of_intervention
        )
        rel_sched = self.generate_relative_schedule(
            self.default_survey, intervention, days_after=days_after
        )
        
        for days_relative in range(days_before * -1, days_after):
            rel_sched.update(days_after=days_relative)
            repopulate_absolute_survey_schedule_events(
                self.default_survey, self.default_participant
            )
            yield days_relative
    
    def test_deleted_participant(self):
        self.INJECT_DEVICE_TRACKER_PARAMS = False
        self.default_participant.update(deleted=True)
        response = self.smart_post_status_code(403)
        self.assertEqual(response.content, b"")
        self.INJECT_DEVICE_TRACKER_PARAMS = True


class TestRegisterParticipant(ParticipantSessionTest):
    ENDPOINT_NAME = "mobile_api.register_user"
    DISABLE_CREDENTIALS = True
    NEW_PASSWORD = "something_new"
    NEW_PASSWORD_HASHED = device_hash(NEW_PASSWORD.encode()).decode()
    
    @property
    def BASIC_PARAMS(self):
        return {
            'patient_id': self.session_participant.patient_id,
            'phone_number': "0000000000",
            'device_id': "pretty_much anything",
            'device_os': "something",
            'os_version': "something",
            "product": "something",
            "brand": "something",
            "hardware_id": "something",
            "manufacturer": "something",
            "model": "something",
            "beiwe_version": "something",
            "new_password": self.NEW_PASSWORD,
            "password": self.DEFAULT_PARTICIPANT_PASSWORD_HASHED
        }
    
    def test_bad_request(self):
        self.skip_next_device_tracker_params
        self.smart_post_status_code(403)
        self.assertIsNone(self.default_participant.last_register_user)
        self.assertIsNone(self.default_participant.first_register_user)
    
    @patch("api.mobile_api.s3_upload")
    @patch("api.mobile_api.get_client_public_key_string")
    def test_first_register_only_triggers_once(
        self, get_client_public_key_string: MagicMock, s3_upload: MagicMock
    ):
        s3_upload.return_value = None
        get_client_public_key_string.return_value = "a_private_key"
        resp = self.smart_post_status_code(200, **self.BASIC_PARAMS)
        # include the basic validity of the request doing its thing test
        response_dict = json.loads(resp.content)
        self.assertEqual("a_private_key", response_dict["client_public_key"])
        self.session_participant.refresh_from_db()
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.assertIsInstance(self.default_participant.first_register_user, datetime)
        self.assertEqual(self.default_participant.last_register_user,
                         self.default_participant.first_register_user)
        old_first_register_user = self.default_participant.first_register_user
        # And then test that the second request Works but doesn't modify the first_register_user.
        # first_register_user should be the same as the last_register_user on these tests.
        response_dict = json.loads(resp.content)
        self.assertEqual("a_private_key", response_dict["client_public_key"])
        self.session_participant.refresh_from_db()
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.assertIsInstance(self.default_participant.first_register_user, datetime)
        self.assertEqual(old_first_register_user, self.default_participant.first_register_user)
        # is also a test for non-equality:
        self.assertGreaterEqual(self.default_participant.last_register_user,
                                self.default_participant.first_register_user)
    
    @patch("api.mobile_api.s3_upload")
    @patch("api.mobile_api.get_client_public_key_string")
    def test_success_unregistered_ever(
        self, get_client_public_key_string: MagicMock, s3_upload: MagicMock
    ):
        # This test has no intervention dates - which is a case that doesn't ~really exist anymore,
        # because loading the participant page will populate those values on all participants where
        # it is missing, with a date value of None. The followup test includes a participant with a
        # None intervention so its probably fine.
        s3_upload.return_value = None
        self.assertIsNone(self.default_participant.last_register_user)
        self.assertIsNone(self.default_participant.first_register_user)  # one off test detail
        get_client_public_key_string.return_value = "a_private_key"
        # unenrolled participants have no device id
        self.session_participant.update(device_id="")
        resp = self.smart_post_status_code(200, **self.BASIC_PARAMS)
        response_dict = json.loads(resp.content)
        self.assertEqual("a_private_key", response_dict["client_public_key"])
        self.session_participant.refresh_from_db()
        self.assertTrue(self.session_participant.validate_password(self.NEW_PASSWORD_HASHED))
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.assertIsInstance(self.default_participant.first_register_user, datetime)
        self.assertEqual(self.default_participant.last_register_user,
                         self.default_participant.first_register_user)
    
    @patch("api.mobile_api.s3_upload")
    @patch("api.mobile_api.get_client_public_key_string")
    def test_success_unregistered_complex_study(
        self, get_client_public_key_string: MagicMock, s3_upload: MagicMock
    ):
        # there was a bug where participants with intervention dates set equal to None would crash
        # inside repopulate_relative_survey_schedule_events because they were not being filtered out,
        # but the bug seems to be a django bug where you can't exclude null values from a queryset.
        s3_upload.return_value = None
        get_client_public_key_string.return_value = "a_private_key"
        self.default_populated_intervention_date.update(date=None)
        self.default_study_field  # may as well throw this in, shouldn't do anything
        # set up a relative schedule that will need to be checked inside repopulate_relative_...
        self.generate_relative_schedule(
            self.default_survey, self.default_intervention, days_after=0
        )
        # run test
        resp = self.smart_post_status_code(200, **self.BASIC_PARAMS)
        response_dict = json.loads(resp.content)
        self.assertEqual("a_private_key", response_dict["client_public_key"])
        self.session_participant.refresh_from_db()
        self.assertTrue(self.session_participant.validate_password(self.NEW_PASSWORD_HASHED))
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.default_populated_intervention_date.refresh_from_db()
        self.assertIsNone(self.default_populated_intervention_date.date)
        # the first_register_user should be the same as the last_register_user on these tests.
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.assertIsInstance(self.default_participant.first_register_user, datetime)
        self.assertEqual(self.default_participant.last_register_user,
                         self.default_participant.first_register_user)
    
    @patch("api.mobile_api.s3_upload")
    @patch("api.mobile_api.get_client_public_key_string")
    def test_success_bad_device_id_still_works(
        self, get_client_public_key_string: MagicMock, s3_upload: MagicMock
    ):
        # we blanket disabled device id validation
        s3_upload.return_value = None
        get_client_public_key_string.return_value = "a_private_key"
        # unenrolled participants have no device id
        params = self.BASIC_PARAMS
        params['device_id'] = "hhhhhhhhhhhhhhhhhhh"
        self.session_participant.update(device_id="aosnetuhsaronceu")
        resp = self.smart_post_status_code(200, **params)
        response_dict = json.loads(resp.content)
        self.assertEqual("a_private_key", response_dict["client_public_key"])
        self.session_participant.refresh_from_db()
        self.assertTrue(self.session_participant.validate_password(self.NEW_PASSWORD_HASHED))
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.assertIsInstance(self.default_participant.first_register_user, datetime)
        self.assertEqual(self.default_participant.last_register_user,
                         self.default_participant.first_register_user)
    
    @patch("api.mobile_api.s3_upload")
    @patch("api.mobile_api.get_client_public_key_string")
    def test_bad_password(self, get_client_public_key_string: MagicMock, s3_upload: MagicMock):
        s3_upload.return_value = None
        get_client_public_key_string.return_value = "a_private_key"
        params = self.BASIC_PARAMS
        params['password'] = "nope!"
        self.skip_next_device_tracker_params
        resp = self.smart_post_status_code(403, **params)
        self.assertEqual(resp.content, b"")
        self.session_participant.refresh_from_db()
        self.assertFalse(self.session_participant.validate_password(self.NEW_PASSWORD_HASHED))
        self.assertIsNone(self.default_participant.last_register_user)
        self.assertIsNone(self.default_participant.first_register_user)
    
    @patch("api.mobile_api.s3_upload")
    @patch("api.mobile_api.get_client_public_key_string")
    def test_study_easy_enrollment(
        self, get_client_public_key_string: MagicMock, s3_upload: MagicMock
    ):
        s3_upload.return_value = None
        get_client_public_key_string.return_value = "a_private_key"
        params = self.BASIC_PARAMS
        self.default_study.update(easy_enrollment=True)
        params['password'] = "nope!"
        resp = self.smart_post_status_code(200, **params)
        response_dict = json.loads(resp.content)
        self.assertEqual("a_private_key", response_dict["client_public_key"])
        self.session_participant.refresh_from_db()
        self.assertTrue(self.session_participant.validate_password(self.NEW_PASSWORD_HASHED))
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.assertIsInstance(self.default_participant.first_register_user, datetime)
        self.assertEqual(self.default_participant.last_register_user,
                         self.default_participant.first_register_user)
    
    @patch("api.mobile_api.s3_upload")
    @patch("api.mobile_api.get_client_public_key_string")
    def test_participant_easy_enrollment(
        self, get_client_public_key_string: MagicMock, s3_upload: MagicMock
    ):
        s3_upload.return_value = None
        get_client_public_key_string.return_value = "a_private_key"
        params = self.BASIC_PARAMS
        self.default_participant.update(easy_enrollment=True)
        params['password'] = "nope!"
        resp = self.smart_post_status_code(200, **params)
        response_dict = json.loads(resp.content)
        self.assertEqual("a_private_key", response_dict["client_public_key"])
        self.session_participant.refresh_from_db()
        self.assertTrue(self.session_participant.validate_password(self.NEW_PASSWORD_HASHED))
        self.assertIsInstance(self.default_participant.last_register_user, datetime)
        self.assertIsInstance(self.default_participant.first_register_user, datetime)
        self.assertEqual(self.default_participant.last_register_user,
                         self.default_participant.first_register_user)
    
    def test_deleted_participant(self):
        self.INJECT_DEVICE_TRACKER_PARAMS = False
        self.default_participant.update(deleted=True)
        response = self.smart_post_status_code(403)
        self.assertEqual(response.content, b"")
        self.INJECT_DEVICE_TRACKER_PARAMS = True
        self.assertIsNone(self.default_participant.last_register_user)
        self.assertIsNone(self.default_participant.first_register_user)


class TestGetLatestDeviceSettings(ParticipantSessionTest):
    ENDPOINT_NAME = "mobile_api.get_latest_device_settings"
    
    def test_success(self):
        p = self.default_participant
        # update the dict below, only very long strings should reference their variables.
        # (I guess id is special too)
        correct_data = {
            'id': self.default_study.device_settings.id,
            'accelerometer': True,
            'gps': True,
            'calls': True,
            'texts': True,
            'wifi': True,
            'bluetooth': False,
            'power_state': True,
            'use_anonymized_hashing': True,
            'use_gps_fuzzing': False,
            'call_clinician_button_enabled': True,
            'call_research_assistant_button_enabled': True,
            'ambient_audio': False,
            'proximity': False,
            'gyro': False,
            'magnetometer': False,
            'devicemotion': False,
            'reachability': True,
            'allow_upload_over_cellular_data': False,
            'accelerometer_off_duration_seconds': 10,
            'accelerometer_on_duration_seconds': 10,
            'accelerometer_frequency': 10,
            'ambient_audio_off_duration_seconds': 600,
            'ambient_audio_on_duration_seconds': 600,
            'ambient_audio_bitrate': 24000,
            'ambient_audio_sampling_rate': 44100,
            'bluetooth_on_duration_seconds': 60,
            'bluetooth_total_duration_seconds': 300,
            'bluetooth_global_offset_seconds': 0,
            'check_for_new_surveys_frequency_seconds': 3600,
            'create_new_data_files_frequency_seconds': 900,
            'gps_off_duration_seconds': 600,
            'gps_on_duration_seconds': 60,
            'seconds_before_auto_logout': 600,
            'upload_data_files_frequency_seconds': 3600,
            'voice_recording_max_time_length_seconds': 240,
            'wifi_log_frequency_seconds': 300,
            'gyro_off_duration_seconds': 600,
            'gyro_on_duration_seconds': 60,
            'gyro_frequency': 10,
            'magnetometer_off_duration_seconds': 600,
            'magnetometer_on_duration_seconds': 60,
            'devicemotion_off_duration_seconds': 600,
            'devicemotion_on_duration_seconds': 60,
            'about_page_text': ABOUT_PAGE_TEXT,
            'call_clinician_button_text': 'Call My Clinician',
            'consent_form_text': CONSENT_FORM_TEXT,
            'survey_submit_success_toast_text': SURVEY_SUBMIT_SUCCESS_TOAST_TEXT,
            'heartbeat_message': DEFAULT_HEARTBEAT_MESSAGE,
            'heartbeat_timer_minutes': 60,
            
            'consent_sections': DEFAULT_CONSENT_SECTIONS,
            
            # Experiment features, yep you gotta manually change it when you change them too.
            # 'enable_binary_uploads': False,
            # 'enable_new_authentication': False,
            # 'enable_developer_datastream': False,
            # 'enable_beta_features': False
            # 'enable_aggressive_background_persistence': False,
            'enable_extensive_device_info_tracking': False,
        }
        
        self.assertIsNone(p.last_get_latest_device_settings)
        response = self.smart_post_status_code(200)
        response_json_loaded = json.loads(response.content.decode())
        
        self.maxDiff = None
        self.assertDictEqual(correct_data, response_json_loaded)
        
        p.refresh_from_db()
        self.assertIsNotNone(p.last_get_latest_device_settings)
        self.assertIsInstance(p.last_get_latest_device_settings, datetime)
    
    def test_deleted_participant(self):
        self.INJECT_DEVICE_TRACKER_PARAMS = False
        self.default_participant.update(deleted=True)
        response = self.smart_post_status_code(403)
        self.assertEqual(response.content, b"")
        self.INJECT_DEVICE_TRACKER_PARAMS = True


#TODO: We don't have a success test because that is insanely complex. We should probably do that.
class TestMobileUpload(ParticipantSessionTest):
    # FIXME: This test needs better coverage
    ENDPOINT_NAME = "mobile_api.upload"
    
    @classmethod
    def setUpClass(cls) -> None:
        # pycrypto (and probably pycryptodome) requires that we re-seed the random number generation
        # if we run using the --parallel directive.
        from Cryptodome import Random as old_Random
        old_Random.atfork()
        return super().setUpClass()
    
    # these are some generated keys that are part of the codebase, because generating them is slow
    # and potentially a source of error.
    with open(f"{BEIWE_PROJECT_ROOT}/tests/files/private_key", 'rb') as f:
        PRIVATE_KEY = get_RSA_cipher(f.read())
    with open(f"{BEIWE_PROJECT_ROOT}/tests/files/public_key", 'rb') as f:
        PUBLIC_KEY = get_RSA_cipher(f.read())
    
    @property
    def assert_no_files_to_process(self):
        self.assertEqual(FileToProcess.objects.count(), 0)
    
    @property
    def assert_one_file_to_process(self):
        self.assertEqual(FileToProcess.objects.count(), 1)
    
    def test_bad_file_names(self):
        self.assert_no_files_to_process
        # responds with 200 code because device deletes file based on return
        self.smart_post_status_code(200)
        self.assert_no_files_to_process
        self.smart_post_status_code(200, file_name="rList")
        self.assert_no_files_to_process
        self.smart_post_status_code(200, file_name="PersistedInstallation")
        self.assert_no_files_to_process
        # valid file extensions: csv, json, mp4, wav, txt, jpg
        self.smart_post_status_code(200, file_name="whatever")
        self.assert_no_files_to_process
        # no file parameter
        self.skip_next_device_tracker_params
        self.smart_post_status_code(400, file_name="whatever.csv")
        self.assert_no_files_to_process
        # correct file key, should fail
        self.smart_post_status_code(200, file="some_content")
        self.assert_no_files_to_process
    
    def test_unregistered_participant(self):
        # fails with 400 if the participant is registered.  This behavior has a side effect of
        # deleting data on the device, which seems wrong.
        self.skip_next_device_tracker_params
        self.smart_post_status_code(400, file_name="whatever.csv")
        self.session_participant.update(permanently_retired=True)
        self.smart_post_status_code(200, file_name="whatever.csv")
        self.assert_no_files_to_process
    
    def test_study_settings_block_uploads(self):
        # manual blocks
        self.default_study.update_only(manually_stopped=True)
        self.smart_post_status_code(200, file_name="whatever.csv")
        self.assert_no_files_to_process
        # manual and end date 10 days ago blocks
        self.default_study.update_only(manually_stopped=True, end_date=date.today() - timedelta(days=10))
        self.smart_post_status_code(200, file_name="whatever.csv")
        self.assert_no_files_to_process
        # just end date 10 days ago blocks
        self.default_study.update_only(manually_stopped=False)
        self.smart_post_status_code(200, file_name="whatever.csv")
        self.assert_no_files_to_process
        # end date yesterday blocks
        self.default_study.update_only(end_date=date.today() - timedelta(days=1))
        self.smart_post_status_code(200, file_name="whatever.csv")
        self.assert_no_files_to_process
        
        # test that an end datedate in the future fully works (errors with missing file is sufficient)
        self.skip_next_device_tracker_params
        self.default_study.update_only(end_date=date.today() + timedelta(days=1))
        resp = self.smart_post_status_code(400, file_name="whatever.csv")
        self.assertEqual(b"file not present", resp.content)
        self.assert_no_files_to_process
        # end date _today_ works.... does this test fail afte if there is a test study timezone shift?
        self.default_study.update_only(end_date=date.today())
        resp = self.smart_post_status_code(400, file_name="whatever.csv")
        self.assertEqual(b"file not present", resp.content)
        self.assert_no_files_to_process
        
        # deleted overrides active study
        self.default_study.update_only(deleted=True)
        self.smart_post_status_code(200, file_name="whatever.csv")
        self.assert_no_files_to_process
        # manual and deleted override end date
        self.default_study.update_only(manually_stopped=True)
        self.smart_post_status_code(200, file_name="whatever.csv")
        self.assert_no_files_to_process
    
    def test_end_study_time_zone_blocks_at_correct_time_of_day(self):
        target_stop_date = date(2020, 1, 31)
        time_zone_name = "Africa/Monrovia"  # literally any random timezone
        target_time_zone = tz.gettz(time_zone_name)
        self.default_study.update_only(end_date=target_stop_date, timezone_name=target_time_zone)
        
        # 8pm
        time_of_day = datetime(2020, 1, 31, 8, 0, 0, tzinfo=target_time_zone)
        # "file not present" is the correct response here , the code to block based on the end time
        # isn't running, so it catches the error.
        with time_machine.travel(time_of_day):
            # 400 code because it is missing the file
            resp = self.smart_post_status_code(400, file_name="whatever.csv")
            self.assert_no_files_to_process
            # It is not blocked by the timezone / end date.
            self.assertEqual(b"file not present", resp.content)
        
        # 9pm
        time_of_day = datetime(2020, 1, 31, 9, 0, 0, tzinfo=target_time_zone)
        with time_machine.travel(time_of_day):
            resp = self.smart_post_status_code(400, file_name="whatever.csv")
            self.assert_no_files_to_process
            # It is not blocked by the timezone / end date.
            self.assertEqual(b"file not present", resp.content)
        
        #10pm
        time_of_day = datetime(2020, 1, 31, 10, 0, 0, tzinfo=target_time_zone)
        with time_machine.travel(time_of_day):
            resp = self.smart_post_status_code(400, file_name="whatever.csv")
            self.assert_no_files_to_process
            # It is not blocked by the timezone / end date.
            self.assertEqual(b"file not present", resp.content)
        
        # 11pm
        time_of_day = datetime(2020, 1, 31, 11, 0, 0, tzinfo=target_time_zone)
        with time_machine.travel(time_of_day):
            resp = self.smart_post_status_code(400, file_name="whatever.csv")
            self.assert_no_files_to_process
            # It is not blocked by the timezone / end date.
            self.assertEqual(b"file not present", resp.content)
        
        # 11:59pm
        time_of_day = datetime(2020, 1, 31, 23, 59, 0, tzinfo=target_time_zone)
        with time_machine.travel(time_of_day):
            resp = self.smart_post_status_code(400, file_name="whatever.csv")
            self.assert_no_files_to_process
            # It is not blocked by the timezone / end date.
            self.assertEqual(b"file not present", resp.content)
        
        # 12am
        time_of_day = datetime(2020, 2, 1, 0, 0, 0, tzinfo=target_time_zone)
        with time_machine.travel(time_of_day):
            # 200 code because it isn't even checking to delete the file, it just tells the device
            # to delete it.
            resp = self.smart_post_status_code(200, file_name="whatever.csv")
            self.assert_no_files_to_process
            # It is blocked by the timezone / end date.
            self.assertEqual(b'study is deleted, stopped, or ended.', resp.content)
        
        # 1am
        time_of_day = datetime(2020, 2, 1, 1, 0, 0, tzinfo=target_time_zone)
        with time_machine.travel(time_of_day):
            resp = self.smart_post_status_code(200, file_name="whatever.csv")
            self.assert_no_files_to_process
            # It is blocked by the timezone / end date.
            self.assertEqual(b'study is deleted, stopped, or ended.', resp.content)
    
    def test_file_already_present_as_ftp(self):
        # there is a ~complex file name test, this value will match and cause that test to succeed,
        # which makes the endpoint return early.  This test will crash with the S3 invalid bucket
        # failure mode if there is no match.
        normalized_file_name = f"{self.session_study.object_id}/whatever.csv"
        self.skip_next_device_tracker_params
        self.smart_post_status_code(400, file_name=normalized_file_name)
        ftp = self.generate_file_to_process(normalized_file_name)
        self.smart_post_status_code(400, file_name=normalized_file_name, file=object())
        self.assert_one_file_to_process
        should_be_identical = FileToProcess.objects.first()
        self.assertEqual(ftp.id, should_be_identical.id)
        self.assertEqual(ftp.last_updated, should_be_identical.last_updated)
        self.assert_one_file_to_process
    
    @patch("libs.participant_file_uploads.s3_upload")
    @patch("database.user_models_participant.Participant.get_private_key")
    def test_no_file_content(self, get_private_key: MagicMock, s3_upload: MagicMock):
        self.assertIsNone(self.default_participant.last_upload)
        get_private_key.return_value = self.PRIVATE_KEY
        self.smart_post_status_code(200, file_name="whatever.csv", file="")
        # big fat nothing happens
        self.assert_no_files_to_process
        self.assertEqual(GenericEvent.objects.count(), 0)
        # inserting this test for the last_upload update....
        self.default_participant.refresh_from_db()
        self.assertIsInstance(self.default_participant.last_upload, datetime)
    
    @patch("libs.participant_file_uploads.s3_upload")
    @patch("database.user_models_participant.Participant.get_private_key")
    def test_decryption_key_bad_padding(self, get_private_key: MagicMock, s3_upload: MagicMock):
        get_private_key.return_value = self.PRIVATE_KEY
        self.smart_post_status_code(200, file_name="whatever.csv", file="some_content")
        self.assert_no_files_to_process
        # happens to be bad length decryption key
        self.assertEqual(GenericEvent.objects.count(), 1)
        self.assertIn("Decryption key not 128 bits", GenericEvent.objects.get().note)
    
    @patch("libs.participant_file_uploads.s3_upload")
    @patch("database.user_models_participant.Participant.get_private_key")
    def test_decryption_key_not_base64(self, get_private_key: MagicMock, s3_upload: MagicMock):
        get_private_key.return_value = self.PRIVATE_KEY
        self.smart_post_status_code(200, file_name="whatever.csv", file="some_content/\\")
        self.assert_no_files_to_process
        self.assertEqual(GenericEvent.objects.count(), 1)
        self.assertIn("Key not base64 encoded:", GenericEvent.objects.get().note)
    
    @patch("libs.participant_file_uploads.s3_upload")
    @patch("database.user_models_participant.Participant.get_private_key")
    def test_bad_base64_length(self, get_private_key: MagicMock, s3_upload: MagicMock):
        get_private_key.return_value = self.PRIVATE_KEY
        self.smart_post_status_code(200, file_name="whatever.csv", file=b"some_content1")
        self.assert_no_files_to_process
        self.assertEqual(GenericEvent.objects.count(), 1)
        self.assertIn(
            "invalid length 2 after padding was removed.",
            GenericEvent.objects.get().note
        )
    
    # TODO: add invalid decrypted key length test...
    
    def test_deleted_participant(self):
        self.INJECT_DEVICE_TRACKER_PARAMS = False
        self.default_participant.update(deleted=True)
        response = self.smart_post_status_code(403)
        self.assertEqual(response.content, b"")
        self.INJECT_DEVICE_TRACKER_PARAMS = True


class TestHeartbeatEndpoint(ParticipantSessionTest):
    ENDPOINT_NAME = "mobile_api.mobile_heartbeat"
    
    # it does one thing
    def test_success(self):
        # test that the heartbeat endpoint creates a heartbeat object
        self.assertEqual(AppHeartbeats.objects.count(), 0)
        self.smart_post_status_code(200)
        self.default_participant.refresh_from_db()
        self.assertEqual(AppHeartbeats.objects.count(), 1)
        t_foreign = AppHeartbeats.objects.first().timestamp
        self.assertIsInstance(t_foreign, datetime)
        # test that the endpoint creates additional heartbeats beyond the first
        self.smart_post_status_code(200)
        self.default_participant.refresh_from_db()
        self.assertEqual(AppHeartbeats.objects.count(), 2)
        t_foreign = AppHeartbeats.objects.last().timestamp
        self.assertIsInstance(t_foreign, datetime)
