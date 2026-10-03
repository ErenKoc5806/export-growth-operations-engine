import unittest

from pilot_engine.retry import (ExternalAttemptState, ExternalNextStep,
                                ReadRetryPolicy, TransientReadError, plan_external_action)


class PilotRetryTests(unittest.TestCase):
    def test_transient_read_has_bounded_backoff(self):
        calls, delays = [], []

        def flaky_read():
            calls.append(1)
            if len(calls) < 3:
                raise TransientReadError("temporary")
            return "result"

        result = ReadRetryPolicy(max_attempts=4, initial_delay_seconds=0.5,
                                 max_delay_seconds=0.75).run(flaky_read, sleep=delays.append)
        self.assertEqual(result, "result")
        self.assertEqual((len(calls), delays), (3, [0.5, 0.75]))

    def test_exhaustion_and_permanent_errors_do_not_loop(self):
        calls, delays = [], []

        def unavailable():
            calls.append(1)
            raise TransientReadError("still unavailable")

        with self.assertRaises(TransientReadError):
            ReadRetryPolicy(max_attempts=3).run(unavailable, sleep=delays.append)
        self.assertEqual((len(calls), delays), (3, [0.5, 1.0]))
        calls.clear()

        def invalid():
            calls.append(1)
            raise ValueError("bad input")

        with self.assertRaises(ValueError):
            ReadRetryPolicy().run(invalid, sleep=delays.append)
        self.assertEqual(len(calls), 1)

    def test_write_outcomes_require_reconciliation_or_review(self):
        expected = {
            ExternalAttemptState.NOT_STARTED: ExternalNextStep.FIRST_ATTEMPT,
            ExternalAttemptState.IN_FLIGHT: ExternalNextStep.RECONCILE_PROVIDER,
            ExternalAttemptState.UNKNOWN: ExternalNextStep.RECONCILE_PROVIDER,
            ExternalAttemptState.CONFIRMED: ExternalNextStep.ALREADY_CONFIRMED,
            ExternalAttemptState.FAILED_SAFE: ExternalNextStep.OPERATOR_REVIEW,
        }
        for state, decision in expected.items():
            with self.subTest(state=state):
                self.assertEqual(plan_external_action(state, "stable-key-1"), decision)
        for state, key in (("GIBBERISH", "stable-key"), (ExternalAttemptState.UNKNOWN, " ")):
            with self.assertRaises(ValueError):
                plan_external_action(state, key)

    def test_invalid_retry_budget_is_rejected(self):
        for kwargs in ({"max_attempts": 0}, {"max_attempts": 100},
                       {"initial_delay_seconds": -1}, {"max_delay_seconds": 31}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ReadRetryPolicy(**kwargs)


if __name__ == "__main__":
    unittest.main()
