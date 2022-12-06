import base64
import codecs
import hashlib
import random
import re
from binascii import Error as base64_error
from hashlib import pbkdf2_hmac as pbkdf2
from os import urandom
from typing import Tuple

from constants.message_strings import NEW_PASSWORD_8_LONG, NEW_PASSWORD_RULES_FAIL
from constants.security_constants import EASY_ALPHANUMERIC_CHARS, PASSWORD_REQUIREMENT_REGEX_LIST


# Seed the random number subsystem with some good entropy.
# This is a security measure, it happens once at import, don't remove.
random.seed(urandom(256))


class DatabaseIsDownError(Exception): pass
class PaddingException(Exception): pass
class Base64LengthException(Exception): pass


################################################################################
################################## HASHING #####################################
################################################################################

# Mongo does not like strings with invalid binary config, so we store binary config
# using url safe base64 encoding.


# noinspection InsecureHash
def chunk_hash(data: bytes) -> bytes:
    """ We need to hash data in a data stream chunk and store the hash in mongo. """
    digest = hashlib.md5(data).digest()
    return codecs.encode(digest, "base64").replace(b"\n", b"")


# noinspection InsecureHash
def device_hash(data: bytes) -> bytes:
    """ Hashes an input string using the sha256 hash, mimicking the hash used on
    the devices.  Expects a string not in base64, returns a base64 string."""
    sha256 = hashlib.sha256()
    sha256.update(data)
    return encode_base64(sha256.digest())


def encode_generic_base64(data: bytes) -> bytes:
    # """ Creates a url safe base64 representation of an input string, strips new lines."""
    return base64.b64encode(data).replace(b"\n", b"")


def encode_base64(data: bytes) -> bytes:
    """ Creates a url safe base64 representation of an input string, strips
        new lines."""
    return base64.urlsafe_b64encode(data).replace(b"\n", b"")


def decode_base64(data: bytes, paddiing_fix=0) -> bytes:
    """ unpacks url safe base64 encoded string. Throws a more obviously named variable when
    encountering a padding error, which just means that there was no base64 padding for base64
    blobs of invalid length (possibly invalid base64 ending characters). """
    try:
        return base64.urlsafe_b64decode(data)
    except base64_error as e:
        # (in python 3.8 the error message is changed to include this information.)
        length = len(data.strip(b"="))
        if length % 4 != 0:
            # stacktrace readers: this means corrupted data.
            raise Base64LengthException(f"Data provided had invalid length {length} after padding was removed.")
        
        if "incorrect padding" in str(e).lower() or "number of data characters" in str(e).lower():
            # for unknown reasons sometimes the padding is wrong, probably on corrupted data.
            # Character counts supposed to be divisible by 4. recurring because its easy.
            if paddiing_fix <= 4:
                paddiing_fix += 1
                padding = b"=" * paddiing_fix
                return decode_base64(data + padding, paddiing_fix=paddiing_fix)
            # str(data) here is correct, we need a representation of the data, not the raw data.
            raise PaddingException(f'{str(e)} -- "{str(data)}"')
        
        raise


def generate_participant_hash_and_salt(password: bytes) -> Tuple[bytes, bytes]:
    """ Generates a hash and salt that will match a given input string, and also
        matches the hashing that is done on a user's device.
        Input is anticipated to be any arbitrary string."""
    salt = encode_base64(urandom(16))
    password = device_hash(password)
    # this iterations value has to be hardcoded without updating the device authentication mechanism
    password_hashed = encode_base64(pbkdf2('sha1', password, salt, iterations=1000, dklen=32))
    return password_hashed, salt


def generate_hash_and_salt(password: bytes) -> Tuple[bytes, bytes]:
    """ Generates a hash and salt that will match for a given input string.
        Input is anticipated to be any arbitrary string."""
    salt = encode_base64(urandom(16))
    # this iterations value has to be hardcoded without updating the device authentication mechanism
    password_hashed = encode_base64(pbkdf2('sha1', password, salt, iterations=1000, dklen=32))
    return password_hashed, salt


def generate_hash_and_salt_sha256(password: bytes, iterations: int) -> Tuple[bytes, bytes]:
    """ Generates a hash and salt that will match for a given input string.
        Input is anticipated to be any arbitrary string."""
    salt = encode_base64(urandom(16))
    password_hashed = encode_base64(pbkdf2('sha256', password, salt, iterations=iterations, dklen=32))
    return password_hashed, salt


def compare_password(proposed_password: bytes, salt: bytes, real_password_hash: bytes) -> bool:
    """ Compares a proposed password with a salt and a real password, returns
        True if the hash results are identical.
        Expects the proposed password to be a base64 encoded string.
        Expects the real password to be a base64 encoded string. """
    # this iterations value has to be hardcoded without updating the device authentication mechanism
    proposed_hash = encode_base64(pbkdf2('sha1', proposed_password, salt, iterations=1000, dklen=32))
    return proposed_hash == real_password_hash


def compare_password_sha256(
    proposed_password: bytes, salt: bytes, real_password_hash: bytes, iterations: int
) -> bool:
    """ Compares a proposed password with a salt and a real password, returns
        True if the hash results are identical.
        Expects the proposed password to be a base64 encoded string.
        Expects the real password to be a base64 encoded string. """
    # this iterations value has to be hardcoded without updating the device authentication mechanism
    proposed_hash = encode_base64(pbkdf2('sha256', proposed_password, salt, iterations=iterations, dklen=32))
    return proposed_hash == real_password_hash


################################################################################
############################### Random #########################################
################################################################################

def generate_easy_alphanumeric_string(length: int = 8) -> str:
    """
    Generates an "easy" alphanumeric (lower case) string of length 8 without the 0 (zero)
    character. This is a design decision, because users will have to type in the "easy"
    string on mobile devices, so we have made this a string that is easy to type and
    easy to distinguish the characters of (e.g. no I/l, 0/o/O confusion).
    """
    return ''.join(random.choice(EASY_ALPHANUMERIC_CHARS) for _ in range(length))


def generate_random_string() -> bytes:
    return encode_generic_base64(hashlib.sha512(urandom(16)).digest())


def check_password_requirements(password) -> (bool, str):
    if len(password) < 8:
        return False, NEW_PASSWORD_8_LONG
    for regex in PASSWORD_REQUIREMENT_REGEX_LIST:
        if not re.search(regex, password):
            return False, NEW_PASSWORD_RULES_FAIL
    return True, None
