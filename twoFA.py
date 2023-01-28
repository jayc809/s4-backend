import pyotp
import random
import qrcode


def generate_twoFA_code(username, key=None):
    if not key:
        key = pyotp.random_base32()
    try:
        uri = pyotp.totp.TOTP(key).provisioning_uri(
            name=username, issuer_name="S4")
        code_img = qrcode.make(uri)
        return code_img, key
    except Exception as e:
        print(e)
        return


def compare_twoFA_code(key, code):
    print(key, code)
    try:
        totp = pyotp.TOTP(key)
        return totp.verify(code)
    except Exception as e:
        print(e)
        return
