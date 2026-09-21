# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""One response limit and wall-clock timeout for every client transport."""

import asyncio
from typing import Any

import httpx

from powercontext.client.errors import ResponseReadError

MAX_RESPONSE_BYTES = 1_048_576


async def request_bounded(
    client: httpx.AsyncClient, method: str, url: str, *, budget_seconds: float, **kwargs: Any
) -> httpx.Response:
    response = None
    try:
        async with asyncio.timeout(budget_seconds):
            async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ResponseReadError(
                            response.url.path,
                            status_code=response.status_code,
                            request_id=response.headers.get("X-PowerContext-Request-ID"),
                            body_error="response_too_large",
                        )
                    content.extend(chunk)
                response._content = bytes(content)
                return response
    except (httpx.HTTPError, TimeoutError) as error:
        if response is None:
            raise
        raise ResponseReadError(
            response.url.path,
            status_code=response.status_code,
            request_id=response.headers.get("X-PowerContext-Request-ID"),
            body_error="request_timeout"
            if isinstance(error, TimeoutError | httpx.TimeoutException)
            else "connection_failed",
        ) from error
