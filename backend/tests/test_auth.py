import unittest

from vanguard_inventory.accounts.auth import hash_password, normalize_email, verify_password


class PasswordSecurityTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self):
        first_hash, first_salt = hash_password("correct horse battery staple")
        second_hash, second_salt = hash_password("correct horse battery staple")

        self.assertNotEqual(first_hash, second_hash)
        self.assertNotEqual(first_salt, second_salt)
        self.assertTrue(verify_password("correct horse battery staple", first_hash, first_salt))
        self.assertFalse(verify_password("wrong password", first_hash, first_salt))

    def test_email_identity_is_case_insensitive(self):
        self.assertEqual(normalize_email("  Person@Example.COM "), "person@example.com")


if __name__ == "__main__":
    unittest.main()
