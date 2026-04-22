import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import bubbles


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id=123):
        self.effective_user = FakeUser(user_id)
        self.message = FakeMessage()


class BubblesCalendarParsingTests(unittest.TestCase):
    def test_generic_schedule_intent_starts_empty_draft(self):
        request = bubbles.extract_event_request("can you schedule a new appointment?")
        self.assertIsNotNone(request)
        self.assertEqual(request["title"], "")
        self.assertIsNone(request["date"])
        self.assertIsNone(request["time"])

    def test_schedule_intent_infers_appointment_type_title(self):
        request = bubbles.extract_event_request("can you schedule a dental appointment?")
        self.assertEqual(request["title"], "Dental appointment")
        self.assertEqual(request["appointment_type"], "dental")

    def test_event_request_extracts_title_date_and_time(self):
        request = bubbles.extract_event_request("Schedule dentist on April 25 at 2:30pm")
        self.assertEqual(request["title"], "Dentist")
        self.assertEqual(request["date"], "2026-04-25")
        self.assertEqual(request["time"], "14:30")

    def test_evening_time_phrases_parse_as_local_pm(self):
        self.assertEqual(bubbles.parse_human_time("April 28th at 7:00 PM"), "19:00")
        self.assertEqual(bubbles.parse_human_time("at 7 in the evening"), "19:00")
        self.assertEqual(bubbles.parse_human_time("Starts at 6 p.m."), "18:00")
        self.assertEqual(bubbles.parse_human_time("at 7"), "07:00")

    def test_day_month_phrase_uses_next_matching_date(self):
        self.assertEqual(bubbles.parse_human_date("25th of April"), "2026-04-25")

    def test_next_weekday_phrase_is_supported(self):
        self.assertEqual(bubbles.parse_human_date("next Friday"), "2026-04-24")

    def test_calendar_range_detection(self):
        self.assertTrue(bubbles.asks_for_calendar_range("show my appointments for seven days"))
        self.assertEqual(bubbles.parse_days_from_text("show my appointments for seven days"), 7)

    def test_ollama_messages_include_persona_and_memory(self):
        bubbles.CHAT_MEMORY[123] = [{"role": "user", "content": "remember this"}]
        messages = bubbles.build_ollama_messages(123, "hello")
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("You are Bubbles", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "user", "content": "remember this"})
        self.assertEqual(messages[2], {"role": "user", "content": "hello"})

    def test_reminder_offsets_parse_natural_replies(self):
        cases = {
            "yes, 30 minutes before": [30],
            "remind me 30 minutes before and one day before": [30, 1440],
            "1 hour before": [60],
            "two reminders: 10 minutes and 1 day before": [10, 1440],
            "day before and 30 mins before": [30, 1440],
            "2 hours before": [120],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                offsets, was_limited = bubbles.parse_reminder_offsets(text)
                self.assertEqual(sorted(offsets), sorted(expected))
                self.assertFalse(was_limited)

    def test_no_reminder_phrases(self):
        for text in ("Nah that's okay", "No I don't want to remind her", "No reminder", "no thanks", "skip"):
            with self.subTest(text=text):
                self.assertTrue(bubbles.is_no_reminder_reply(text))
                self.assertEqual(bubbles.parse_reminder_offsets(text), ([], False))

    def test_reminder_offsets_limit_to_five(self):
        offsets, was_limited = bubbles.parse_reminder_offsets(
            "5 minutes, 10 minutes, 15 minutes, 30 minutes, 1 hour, 1 day before"
        )
        self.assertEqual(offsets, [5, 10, 15, 30, 60])
        self.assertTrue(was_limited)

    def test_memory_save_load_and_default_location_reuse(self):
        original_path = bubbles.MEMORY_PATH
        with TemporaryDirectory() as tmpdir:
            bubbles.MEMORY_PATH = Path(tmpdir) / "memory.json"
            bubbles.update_appointment_default("dental", "location", "Scottsdale Dental")
            self.assertEqual(
                bubbles.appointment_defaults("dental")["location"],
                "Scottsdale Dental",
            )
            draft = {"appointment_type": "dental", "title": "Dental appointment"}
            notes = bubbles.apply_saved_defaults(draft)
            self.assertEqual(draft["location"], "Scottsdale Dental")
            self.assertEqual(bubbles.appointment_defaults("dental")["usual_location"], "Scottsdale Dental")
            self.assertIn("Using Scottsdale Dental", notes[0])
        bubbles.MEMORY_PATH = original_path

    def test_memory_fact_parser(self):
        self.assertEqual(
            bubbles.parse_memory_fact("Scottsdale Dental is my usual dentist location"),
            ("dental", "Scottsdale Dental"),
        )
        self.assertEqual(
            bubbles.parse_memory_fact(
                "At my usual dentist location I’ve already told you. Please add Scottsdale Dental as my dentist"
            ),
            ("dental", "Scottsdale Dental"),
        )

    def test_location_reply_extracts_place_not_instruction_text(self):
        location, should_save, appointment_type = bubbles.extract_location_reply(
            "At my usual dentist location I’ve already told you. Please add Scottsdale Dental as my dentist",
            "dental",
        )
        self.assertEqual(location, "Scottsdale Dental")
        self.assertTrue(should_save)
        self.assertEqual(appointment_type, "dental")

        location, should_save, appointment_type = bubbles.extract_location_reply("at Scottsdale Dental", "dental")
        self.assertEqual(location, "Scottsdale Dental")
        self.assertFalse(should_save)
        self.assertIsNone(appointment_type)

    def test_calendar_event_payload_preserves_local_wall_time(self):
        class FakeExecute:
            def __init__(self, payload):
                self.payload = payload

            def execute(self):
                return self.payload

        class FakeEvents:
            def insert(self, calendarId, body):
                return FakeExecute(body)

        class FakeService:
            def events(self):
                return FakeEvents()

        original_get_service = bubbles.get_calendar_service
        bubbles.get_calendar_service = lambda: FakeService()
        try:
            event = bubbles.create_calendar_event(
                "Dental appointment",
                "2026-04-28 19:00",
                True,
                "0",
                duration_minutes=60,
                location="Scottsdale Dental",
                reminder_offsets=[30, 1440],
            )
        finally:
            bubbles.get_calendar_service = original_get_service

        self.assertEqual(event["start"]["dateTime"], "2026-04-28T19:00:00")
        self.assertEqual(event["start"]["timeZone"], bubbles.GOOGLE_CALENDAR_TIMEZONE)
        self.assertEqual(event["end"]["dateTime"], "2026-04-28T20:00:00")
        self.assertEqual(event["location"], "Scottsdale Dental")
        self.assertEqual(
            event["reminders"]["overrides"],
            [{"method": "popup", "minutes": 30}, {"method": "popup", "minutes": 1440}],
        )

    def test_event_formatting_uses_separate_lines(self):
        event = {
            "summary": "Bingo",
            "start": {"dateTime": "2026-04-22T18:00:00"},
            "end": {"dateTime": "2026-04-22T20:00:00"},
            "location": "Virtual",
        }
        formatted = bubbles.format_event_for_telegram(event)
        self.assertIn("📌 Bingo", formatted)
        self.assertIn("🗓️ Wednesday, April 22", formatted)
        self.assertIn("⏰ 6:00 PM - 8:00 PM", formatted)
        self.assertIn("📍 Virtual", formatted)


class BubblesReminderStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bubbles.PENDING_EVENTS.clear()
        bubbles.CHAT_MEMORY.clear()
        self.original_memory_path = bubbles.MEMORY_PATH
        self.tmpdir = TemporaryDirectory()
        bubbles.MEMORY_PATH = Path(self.tmpdir.name) / "memory.json"

    async def asyncTearDown(self):
        bubbles.MEMORY_PATH = self.original_memory_path
        self.tmpdir.cleanup()

    async def test_reminder_state_accepts_natural_offsets(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "reminders",
            "title": "Dental appointment",
            "date": "2026-04-28",
            "time": "19:00",
            "duration_minutes": 60,
            "location": "Scottsdale Dental",
        }

        handled = await bubbles.continue_event_draft(
            update,
            "Ah yeah remind me 30 minutes before and one day before",
        )

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "description")
        self.assertEqual(bubbles.PENDING_EVENTS[123]["reminder_offsets"], [30, 1440])
        self.assertIn("Any description", update.message.replies[-1])

    async def test_yes_without_details_asks_for_times(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "reminders",
            "title": "Dental appointment",
            "date": "2026-04-28",
            "time": "19:00",
            "duration_minutes": 60,
            "location": "Scottsdale Dental",
        }

        handled = await bubbles.continue_event_draft(update, "yes")

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "reminder_times")
        self.assertIn("when should I remind you", update.message.replies[-1])

    async def test_count_only_after_count_question_asks_for_actual_times(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "reminder_count",
            "title": "Dental appointment",
            "date": "2026-04-28",
            "time": "19:00",
            "duration_minutes": 60,
            "location": "Scottsdale Dental",
        }

        handled = await bubbles.continue_event_draft(update, "Two")

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "reminder_times")
        self.assertEqual(bubbles.PENDING_EVENTS[123]["requested_reminder_count"], 2)
        self.assertIn("two reminder times", update.message.replies[-1].lower())

    async def test_location_state_saves_usual_location_without_instruction_text(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "location",
            "title": "Dental appointment",
            "appointment_type": "dental",
            "date": "2026-04-28",
            "time": "19:00",
            "duration_minutes": 60,
        }

        handled = await bubbles.continue_event_draft(
            update,
            "At my usual dentist location I’ve already told you. Please add Scottsdale Dental as my dentist",
        )

        self.assertTrue(handled)
        draft = bubbles.PENDING_EVENTS[123]
        self.assertEqual(draft["location"], "Scottsdale Dental")
        self.assertNotIn("already told you", draft["location"])
        self.assertEqual(draft["step"], "reminders")
        self.assertEqual(bubbles.appointment_defaults("dental")["usual_location"], "Scottsdale Dental")
        self.assertIn("Using Scottsdale Dental as your usual dental location.", update.message.replies[-1])

    async def test_same_as_usual_reuses_saved_location(self):
        bubbles.update_appointment_default("dental", "location", "Scottsdale Dental")
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "location",
            "title": "Dental appointment",
            "appointment_type": "dental",
            "date": "2026-04-28",
            "time": "19:00",
            "duration_minutes": 60,
        }

        handled = await bubbles.continue_event_draft(update, "same as usual")

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["location"], "Scottsdale Dental")
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "reminders")

    async def test_no_reminder_reply_advances_without_repeating_prompt(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "reminders",
            "title": "Bingo",
            "date": "2026-04-22",
            "time": "18:00",
            "duration_minutes": 120,
            "location": "Virtual",
        }

        handled = await bubbles.continue_event_draft(update, "Nah that's okay")

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["reminder_offsets"], [])
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "description")
        self.assertNotIn("When should I remind you", update.message.replies[-1])

    async def test_no_reminder_reply_in_reminder_times_advances(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "reminder_times",
            "title": "Bingo",
            "date": "2026-04-22",
            "time": "18:00",
            "duration_minutes": 120,
            "location": "Virtual",
        }

        handled = await bubbles.continue_event_draft(update, "No I don't want to remind her")

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["reminder_offsets"], [])
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "description")

    async def test_optional_location_accepts_virtual_and_skip(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "location",
            "title": "Bingo",
            "date": "2026-04-22",
            "time": "18:00",
            "duration_minutes": 120,
        }

        handled = await bubbles.continue_event_draft(update, "virtual")

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["location"], "Virtual")
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "reminders")

        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "location",
            "title": "Bingo",
            "date": "2026-04-22",
            "time": "18:00",
            "duration_minutes": 120,
        }

        handled = await bubbles.continue_event_draft(update, "skip")

        self.assertTrue(handled)
        self.assertEqual(bubbles.PENDING_EVENTS[123]["location"], "")
        self.assertTrue(bubbles.PENDING_EVENTS[123]["location_skipped"])
        self.assertEqual(bubbles.PENDING_EVENTS[123]["step"], "reminders")

    async def test_retry_counter_changes_prompt_after_two_invalid_answers(self):
        update = FakeUpdate()
        bubbles.PENDING_EVENTS[123] = {
            "step": "time",
            "title": "Bingo",
            "date": "2026-04-22",
        }

        await bubbles.continue_event_draft(update, "later")
        await bubbles.continue_event_draft(update, "after dinner")

        self.assertIn("Please send a start time", update.message.replies[-1])


if __name__ == "__main__":
    unittest.main()
