import random
import string
import unittest


class MyTestCase(unittest.TestCase):

    def test_something(self):
        for idx in range(100):
            print("String:", self.get_random_string(16))

    def get_random_string(self, num: int) -> str:
        return ''.join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(num))


if __name__ == '__main__':
    unittest.main()
