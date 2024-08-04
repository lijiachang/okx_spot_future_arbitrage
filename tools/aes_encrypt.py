# -*— coding:utf-8 -*-
from Crypto.Cipher import AES


class AesEncrypt:
    """AES encrypt.
    https://pycryptodome.readthedocs.io/en/latest/src/cipher/cipher.html

    Attributes:
        key: AES key, length must be 16|24|32.
    """

    def __init__(self, key):
        self.key = key.encode("utf8")  # length must be 16|24|32.

        self.mode = AES.MODE_CBC
        self.IV = b"0000000000000000"

    def encrypt(self, text):
        from binascii import b2a_hex

        if isinstance(text, bytes):
            text = text.decode()
        cryptor = AES.new(self.key, self.mode, self.IV)
        length = 16
        count = len(text)
        if count % length != 0:
            add = length - (count % length)
        else:
            add = 0
        text = text + ("\0" * add)
        ciphertext = cryptor.encrypt(text.encode())
        return b2a_hex(ciphertext).decode()

    def decrypt(self, text):
        from binascii import a2b_hex

        from Crypto.Cipher import AES

        cryptor = AES.new(self.key, self.mode, self.IV)
        if not isinstance(text, bytes):
            text = text.encode()
        plain_text = cryptor.decrypt(a2b_hex(text)).decode()
        return plain_text.rstrip("\0")


if __name__ == "__main__":
    aes_client = AesEncrypt("1231231238888888")
    data = "65957DFEB5CE42FA7F2CCF19212ADC19"
    encrypted = aes_client.encrypt(data)
    print(f"encrypted: {encrypted}")
    decrypted = aes_client.decrypt(encrypted)
    print(f"decrypted: {decrypted}")
