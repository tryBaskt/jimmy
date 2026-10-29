"""Bounded MCP Streamable HTTP client for the fixed Bigdata remote server."""

import json
import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, SchemaError

ENDPOINT = 'https://mcp.bigdata.com/'
READ_TOOLS = {'bigdata_search', 'bigdata_screen_companies', 'find_securities', 'get_securities'}


class BigdataMCP:
    def __init__(self, api_key, notify=lambda *args: None):
        self._key, self.notify = api_key, notify
        self._tools, self._http, self._session, self._sequence = None, None, None, 0
        self._version = '2025-03-26'
        self.calls = 0

    def _rpc(self, method, params=None, notification=False):
        self._sequence += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if not notification:
            body['id'] = self._sequence
        if params is not None:
            body['params'] = params
        headers = {'x-api-key': self._key, 'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': self._version}
        if self._session:
            headers['Mcp-Session-Id'] = self._session
        try:
            with self._http.stream('POST', ENDPOINT, json=body, headers=headers) as response:
                if response.status_code not in {200, 202}:
                    raise ValueError(f'Bigdata MCP HTTP {response.status_code}')
                if response.headers.get('mcp-session-id'):
                    self._session = response.headers['mcp-session-id']
                if notification:
                    return {}
                if 'text/event-stream' in response.headers.get('content-type', ''):
                    event, size = [], 0
                    for line in response.iter_lines():
                        size += len(line.encode())
                        if size > 40 * 1024 * 1024:
                            raise ValueError('Bigdata MCP response exceeds size budget')
                        if line.startswith('data:'):
                            event.append(line[5:].lstrip())
                        elif line == '' and event:
                            message = json.loads('\n'.join(event))
                            event = []
                            if message.get('id') == body['id']:
                                return self._result(message)
                    raise ValueError('Bigdata MCP stream ended without a matching response')
                raw = b''.join(self._bounded(response.iter_bytes()))
                message = json.loads(raw)
                if message.get('id') != body['id']:
                    raise ValueError('Bigdata MCP response ID mismatch')
                return self._result(message)
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise ValueError(f'Bigdata MCP transport failure ({type(error).__name__})') from None

    @staticmethod
    def _bounded(chunks):
        size = 0
        for chunk in chunks:
            size += len(chunk)
            if size > 40 * 1024 * 1024:
                raise ValueError('Bigdata MCP response exceeds size budget')
            yield chunk

    @staticmethod
    def _result(message):
        if 'error' in message:
            # Error text and headers can contain credentials or private tool context.
            raise ValueError(f'Bigdata MCP protocol error {message["error"].get("code", "unknown")}')
        return message.get('result', {})

    def tools(self):
        if self._tools is not None:
            return self._tools
        if not self._key:
            raise ValueError('Bigdata MCP credentials are not configured')
        self._http = httpx.Client(timeout=httpx.Timeout(120, connect=10), follow_redirects=False)
        result = self._rpc('initialize', {'protocolVersion': self._version, 'capabilities': {},
                                         'clientInfo': {'name': 'jimmy-paper-replication', 'version': '1'}})
        self._version = result.get('protocolVersion', self._version)
        self._rpc('notifications/initialized', notification=True)
        tools, cursor = {}, None
        for _ in range(4):
            page = self._rpc('tools/list', {'cursor': cursor} if cursor else {})
            for tool in page.get('tools', []):
                tools[tool['name']] = tool
            next_cursor = page.get('nextCursor')
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise ValueError('Bigdata MCP tool pagination repeated')
            cursor = next_cursor
        else:
            raise ValueError('Bigdata MCP tool inventory exceeds discovery budget')
        self._tools = tools
        self.notify('mcp_connected', 'Bigdata MCP tool inventory discovered',
                    {'endpoint': ENDPOINT, 'protocol_version': self._version,
                     'available_tools': sorted(tools),
                     'historical_daily_prices_tool': 'not exposed by supported read tools'})
        return tools

    def call(self, name, arguments):
        if name not in READ_TOOLS:
            raise ValueError('MCP tool is not an authorized read-only data capability')
        tool = self.tools().get(name)
        if not tool:
            raise ValueError(f'Bigdata MCP capability unavailable: {name}')
        try:
            def reject_external_references(value):
                if isinstance(value, dict):
                    if '$ref' in value and not value['$ref'].startswith('#'):
                        raise ValueError('External MCP schema references are unsupported')
                    for child in value.values():
                        reject_external_references(child)
                elif isinstance(value, list):
                    for child in value:
                        reject_external_references(child)
            reject_external_references(tool['inputSchema'])
            Draft202012Validator.check_schema(tool['inputSchema'])
            Draft202012Validator(tool['inputSchema']).validate(arguments)
        except (ValidationError, SchemaError):
            raise ValueError(f'Bigdata MCP arguments do not match discovered schema: {name}') from None
        self.calls += 1
        if self.calls > 60:
            raise ValueError('Bigdata MCP tool-call budget exhausted')
        self.notify('mcp_tool_started', f'Calling Bigdata MCP {name}', {'tool': name, 'arguments': arguments})
        result = self._rpc('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError'):
            raise ValueError(f'Bigdata MCP tool returned an error: {name}')
        self.notify('mcp_tool_completed', f'Bigdata MCP {name} completed', {'tool': name})
        return self.structured(result)

    @staticmethod
    def structured(result):
        if isinstance(result.get('structuredContent'), dict):
            return result['structuredContent']
        for content in result.get('content', []):
            if content.get('type') != 'text':
                continue
            text = content.get('text', '').strip()
            try:
                value = json.loads(text)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                pass
        raise ValueError('Bigdata MCP tool did not return structured data; narrative cannot be a dataset')

    def close(self):
        if self._http:
            self._http.close()
