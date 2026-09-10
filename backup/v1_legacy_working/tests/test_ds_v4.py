from __future__ import annotations

import unittest

from mwoif.ds_v4 import (
    chacha20_block,
    decode_v4_data_b64,
    decode_v4_form_body,
    encode_v4,
    fastlz_level1_decompress,
    fastlz_level1_literal_compress,
)


class DsV4Tests(unittest.TestCase):
    def test_chacha20_rfc8439_block_vector(self):
        key = bytes(range(32))
        nonce = bytes.fromhex("000000090000004a00000000")
        block = chacha20_block(key, nonce, 1)
        self.assertEqual(
            block.hex(),
            "10f1e7e4d13b5915500fdd1fa32071c4"
            "c7d1f4c733c068030422aa9ac3d46c4e"
            "d2826446079faa0914c2d705d98b02a2"
            "b5129cd1de164eb9cbd083e8a2503c4e",
        )

    def test_fastlz_literal_stream_roundtrip(self):
        raw = (b"hello-heart-ds-" * 80) + b"tail"
        compressed = fastlz_level1_literal_compress(raw)
        self.assertEqual(
            fastlz_level1_decompress(compressed, len(raw)),
            raw,
        )

    def test_ds_v4_roundtrip(self):
        raw = b'{"pid":"game/sendLifeMail2.ds","memberSeq":123,"requestId":""}'
        encoded = encode_v4(raw, padding_spaces=15)
        decoded = decode_v4_form_body(encoded.form_body)
        self.assertEqual(decoded, raw + (b" " * 15))
        self.assertTrue(encoded.form_body.startswith(b"isEncryptedData=4&data="))

    def test_response_base64_without_equals_padding(self):
        raw = b'{"mailList":[]}'
        encoded = encode_v4(raw, padding_spaces=3)
        decoded = decode_v4_data_b64(encoded.data_b64.rstrip("="))
        self.assertEqual(decoded.rstrip(b" "), raw)


if __name__ == "__main__":
    unittest.main()
