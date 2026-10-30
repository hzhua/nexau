
### MCP

NexAU delegates MCP protocol negotiation, stdio, Streamable HTTP, legacy SSE,
and OAuth to the official MCP Python SDK. Configure each connection in the
Agent's `mcp_servers` list.

#### Python configuration

```python
import os

from nexau import Agent, AgentConfig, LLMConfig

llm_config = LLMConfig(
    model=os.getenv("LLM_MODEL"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("LLM_API_KEY"),
)

mcp_servers = [
    {
        "name": "amap-maps-streamableHTTP",
        "type": "http",
        "url": "https://mcp.example.com/mcp",
        "headers": {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        },
        "timeout": 10
    }
]

agent_config = AgentConfig(
    name="amap_agent",
    system_prompt="""You are an AI agent with access to Amap Maps services through MCP.

You can use Amap Maps tools to:
- Search for locations and points of interest
- Get directions and navigation information
- Calculate distances and travel times
- Find nearby businesses and services
- Access real-time traffic information
- And other location-based services

When using map tools, always provide clear and helpful information to users.
Explain what you're doing and provide context for the results.""",
    mcp_servers=mcp_servers,
    llm_config=llm_config,
)
agent = Agent(config=agent_config)

response = agent.run("现在从漕河泾现代服务园A6到上南路 4265弄要多久？")
print(response)
```

#### YAML configuration

The following Agent config demonstrates every supported transport and auth
mode. Secret values are always referenced by environment variable name; do not
place tokens or client secrets in YAML.

```yaml
mcp_servers:
  # Official SDK stdio transport. Only the SDK safe environment allow-list and
  # this explicit env mapping are passed to the child process.
  - name: local_tools
    type: stdio
    command: python
    args: ["./local_mcp_server.py"]
    env:
      SERVER_PROFILE: development
    timeout: 30

  # Streamable HTTP with arbitrary non-authentication headers.
  - name: internal_http
    type: http
    url: "https://mcp.example.com/mcp"
    headers:
      X-Tenant: north
    timeout: 30

  # Legacy SSE transport with a backward-compatible Authorization header.
  - name: legacy_sse
    type: sse
    url: "https://legacy-mcp.example.com/sse"
    headers:
      Authorization: "Bearer legacy-token"

  # Static Bearer auth resolved from the environment.
  - name: bearer_http
    type: http
    url: "https://bearer-mcp.example.com/mcp"
    auth:
      type: bearer
      token:
        source: env
        key: BEARER_MCP_TOKEN

  # Interactive OAuth 2.0 Authorization Code + PKCE. Discovery, client
  # registration, token exchange and refresh are handled by the official SDK.
  - name: user_oauth
    type: http
    url: "https://oauth-mcp.example.com/mcp"
    auth:
      type: authorization_code
      client_name: NexAU CLI
      scopes: ["tools:read", "tools:call"]

  # OAuth 2.0 Client Credentials for service-to-service connections.
  - name: service_oauth
    type: http
    url: "https://service-mcp.example.com/mcp"
    auth:
      type: client_credentials
      client_id: nexau-service
      client_secret:
        source: env
        key: SERVICE_MCP_CLIENT_SECRET
      scopes: ["tools:call"]
```

`auth` and an `Authorization` header are mutually exclusive.

### HTTP compatibility

HTTP and SSE servers accept remote HTTP URLs by default, so existing MCP
configurations continue to work without changes. The per-server
`allow_insecure_http` option defaults to `true` and accepts only a boolean.
Set it to `false` to require HTTPS for that server's remote URL. HTTP remains
allowed for `localhost`, `127.0.0.1`, and `::1` with either setting.

```yaml
mcp_servers:
  - name: internal_service
    type: http
    url: http://mcp.internal:8080/mcp
    # allow_insecure_http: true is the default.

  - name: secure_service
    type: sse
    url: https://mcp.example.com/sse
    allow_insecure_http: false
```

The same option is available in Python server dictionaries and `MCPServerConfig`.
HTTP sends credentials and MCP request/response contents without TLS encryption;
use HTTPS for connections that need transport confidentiality. This option
controls the configured MCP server URL, not OAuth provider endpoint or redirect
URI policy, and does not disable TLS certificate verification for HTTPS URLs.
