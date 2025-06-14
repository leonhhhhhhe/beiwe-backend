"""
This code is modified from the django-stubs library, which can be found at:
https://github.com/typeddjango/django-stubs

The changes here are small, mostly it fixes the typing of the HttpStreamingResponse.streaming_content
property not working and causing b"".joitn(streaming_content) to emit a type error in linting.
I filed an bug reoport at https://github.com/typeddjango/django-stubs/issues/2679

This code is distributed under the MIT License and should not be treated as part of the Beiwe project or the property of Onnela Lab at Harvard University.



Copyright (c) Maxim Kurnikov. All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


"""



import datetime
from collections.abc import Iterable, Iterator
from http.cookies import SimpleCookie
from io import BytesIO
from json import JSONEncoder
from typing import Any, Literal, overload, TypeVar

from django.utils.datastructures import CaseInsensitiveMapping


class BadHeaderError(ValueError): ...

_Z = TypeVar("_Z")

class ResponseHeaders(CaseInsensitiveMapping[str]):
    def __init__(self, data: dict[str, str]) -> None: ...
    def _convert_to_charset(self, value: bytes | str | int, charset: str, mime_encode: bool = ...) -> str: ...
    def __delitem__(self, key: str) -> None: ...
    def __setitem__(self, key: str, value: str | bytes | int) -> None: ...
    def pop(self, key: str, default: _Z = ...) -> _Z | tuple[str, str]: ...
    def setdefault(self, key: str, value: str | bytes | int) -> None: ...


class HttpResponseBase:
    status_code: int
    streaming: bool
    cookies: SimpleCookie
    closed: bool
    headers: ResponseHeaders
    def __init__(
        self,
        content_type: str | None = ...,
        status: int | None = ...,
        reason: str | None = ...,
        charset: str | None = ...,
        headers: dict[str, str] | None = ...,
    ) -> None: ...
    @property
    def reason_phrase(self) -> str: ...
    @reason_phrase.setter
    def reason_phrase(self, value: str) -> None: ...
    @property
    def charset(self) -> str: ...
    @charset.setter
    def charset(self, value: str) -> None: ...
    def serialize_headers(self) -> bytes: ...
    __bytes__ = serialize_headers
    def __setitem__(self, header: str, value: str | bytes | int) -> None: ...
    def __delitem__(self, header: str) -> None: ...
    def __getitem__(self, header: str) -> str: ...
    def has_header(self, header: str) -> bool: ...
    def __contains__(self, header: str) -> bool: ...
    def items(self) -> Iterable[tuple[str, str]]: ...
    @overload
    def get(self, header: str, alternate: str) -> str: ...
    @overload
    def get(self, header: str, alternate: None = None) -> str | None: ...
    def set_cookie(
        self,
        key: str,
        value: str = ...,
        max_age: int | datetime.timedelta | None = ...,
        expires: str | datetime.datetime | None = ...,
        path: str = ...,
        domain: str | None = ...,
        secure: bool = ...,
        httponly: bool = ...,
        samesite: Literal["Lax", "Strict", "None", False] | None = ...,
    ) -> None: ...
    def setdefault(self, key: str, value: str) -> None: ...
    def set_signed_cookie(self, key: str, value: str, salt: str = ..., **kwargs: Any) -> None: ...
    def delete_cookie(
        self,
        key: str,
        path: str = ...,
        domain: str | None = ...,
        samesite: Literal["Lax", "Strict", "None", False] | None = ...,
    ) -> None: ...
    def make_bytes(self, value: object) -> bytes: ...
    def close(self) -> None: ...
    def write(self, content: str | bytes) -> None: ...
    def flush(self) -> None: ...
    def tell(self) -> int: ...
    def readable(self) -> bool: ...
    def seekable(self) -> bool: ...
    def writable(self) -> bool: ...
    def writelines(self, lines: Iterable[object]) -> None: ...

    # Fake methods that are implemented by all subclasses
    def __iter__(self) -> Iterator[bytes]: ...
    def getvalue(self) -> bytes: ...

class HttpResponse(HttpResponseBase, Iterable[bytes]):
    content: bytes
    csrf_cookie_set: bool
    sameorigin: bool
    test_server_port: str
    test_was_secure_request: bool
    xframe_options_exempt: bool
    def __init__(self, content: object = ..., *args: Any, **kwargs: Any) -> None: ...
    def serialize(self) -> bytes: ...
    __bytes__ = serialize
    def __iter__(self) -> Iterator[bytes]: ...
    def getvalue(self) -> bytes: ...
    def text(self) -> str: ...

class StreamingHttpResponse(HttpResponseBase, Iterable[bytes]):
    is_async: bool
    # streaming_content = _PropertyDescriptor[
    #     Iterable[object] | AsyncIterable[object], Iterator[bytes] | AsyncIterator[bytes]
    # ]()
    streaming_content: list[bytes]#Iterator[bytes]
    def __init__(
        self, streaming_content: Iterable[object] = ..., *args: Any, **kwargs: Any
    ) -> None: ...
    def __iter__(self) -> Iterator[bytes]: ...
    # def __aiter__(self) -> AsyncIterator[bytes]: ...
    def getvalue(self) -> bytes: ...
    # @property
    # def text(self) -> NoReturn: ...

class FileResponse(StreamingHttpResponse):
    file_to_stream: BytesIO | None
    block_size: int
    as_attachment: bool
    filename: str
    def __init__(self, *args: Any, as_attachment: bool = ..., filename: str = ..., **kwargs: Any) -> None: ...
    def set_headers(self, filelike: BytesIO) -> None: ...

class HttpResponseRedirectBase(HttpResponse):
    allowed_schemes: list[str]
    def __init__(self, redirect_to: str, preserve_request: bool = False, *args: Any, **kwargs: Any) -> None: ...
    @property
    def url(self) -> str: ...

class HttpResponseRedirect(HttpResponseRedirectBase):
    status_code_preserve_request: int

class HttpResponsePermanentRedirect(HttpResponseRedirectBase):
    status_code_preserve_request: int

class HttpResponseNotModified(HttpResponse):
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

class HttpResponseBadRequest(HttpResponse): ...
class HttpResponseNotFound(HttpResponse): ...
class HttpResponseForbidden(HttpResponse): ...

class HttpResponseNotAllowed(HttpResponse):
    def __init__(self, permitted_methods: Iterable[str], *args: Any, **kwargs: Any) -> None: ...

class HttpResponseGone(HttpResponse): ...
class HttpResponseServerError(HttpResponse): ...
class Http404(Exception): ...

class JsonResponse(HttpResponse):
    def __init__(
        self,
        data: Any,
        encoder: type[JSONEncoder] = ...,
        safe: bool = ...,
        json_dumps_params: dict[str, Any] | None = ...,
        **kwargs: Any,
    ) -> None: ...
