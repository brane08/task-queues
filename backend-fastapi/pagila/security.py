import binascii
from base64 import b64decode
from fastapi.exceptions import HTTPException
from fastapi.openapi.models import HTTPBase as HTTPBaseModel
from fastapi.security.http import HTTPBase, HTTPBasicCredentials
from fastapi.security.utils import get_authorization_scheme_param
from starlette.requests import Request
from starlette.status import HTTP_401_UNAUTHORIZED
from typing import Optional


# This would handle specific cases for authentication headers
class HTTPBasic(HTTPBase):

    def __init__(self, *, scheme: str, scheme_name: Optional[str] = None, realm: Optional[str] = None,
                 description: Optional[str] = None, auto_error: bool = True):
        super().__init__(scheme=scheme, scheme_name=scheme_name, description=description, auto_error=auto_error)
        self.model = HTTPBaseModel(scheme="basic", description=description)
        self.scheme_name = scheme_name or self.__class__.__name__
        self.realm = realm
        self.auto_error = auto_error

    async def __call__(self, request: Request) -> Optional[HTTPBasicCredentials]:
        authorization: str = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if self.realm:
            unauthorized_headers = {"WWW-Authenticate": f'Basic realm="{self.realm}"'}
        else:
            unauthorized_headers = {"WWW-Authenticate": "Basic"}
        invalid_user_credentials_exc = HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers=unauthorized_headers,
        )
        if not authorization or scheme.lower() != "basic":
            if self.auto_error:
                raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Not authenticated",
                                    headers=unauthorized_headers)
            else:
                return None
        try:
            data = b64decode(param).decode("ascii")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            raise invalid_user_credentials_exc
        username, separator, password = data.partition(":")
        if not separator:
            raise invalid_user_credentials_exc
        return HTTPBasicCredentials(username=username, password=password)
