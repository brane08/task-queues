import hashlib


class Encryptor:

    @classmethod
    def sha512_hash(cls, input_str: str) -> str:
        return hashlib.sha512(input_str.encode()).hexdigest()

    @classmethod
    def sha384_hash(cls, input_str: str) -> str:
        return hashlib.sha384(input_str.encode()).hexdigest()
