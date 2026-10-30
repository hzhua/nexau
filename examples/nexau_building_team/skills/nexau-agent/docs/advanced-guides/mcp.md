### MCP

NexAU uses the official MCP Python SDK for protocol negotiation, stdio,
Streamable HTTP, legacy SSE, and OAuth 2.0. Configure connections through the
Agent's `mcp_servers` list.

```yaml
mcp_servers:
  - name: local_stdio
    type: stdio
    command: python
    args: ["./local_mcp_server.py"]
    env:
      SERVER_PROFILE: development

  - name: streamable_http
    type: http
    url: https://mcp.example.com/mcp
    headers:
      X-Tenant: north

  - name: legacy_sse
    type: sse
    url: https://legacy-mcp.example.com/sse

  - name: static_bearer
    type: http
    url: https://bearer-mcp.example.com/mcp
    auth:
      type: bearer
      token:
        source: env
        key: BEARER_MCP_TOKEN

  - name: oauth_user
    type: http
    url: https://oauth-mcp.example.com/mcp
    auth:
      type: authorization_code
      client_name: NexAU CLI
      scopes: ["tools:read", "tools:call"]

  - name: oauth_service
    type: http
    url: https://service-mcp.example.com/mcp
    auth:
      type: client_credentials
      client_id: nexau-service
      client_secret:
        source: env
        key: SERVICE_MCP_CLIENT_SECRET
      scopes: ["tools:call"]
```

Secret values are environment references, not YAML literals. `auth` and an
`Authorization` header cannot be configured together. HTTP/SSE servers accept
remote HTTP by default for compatibility with existing configurations.
Set the per-server boolean `allow_insecure_http: false` to require HTTPS for
remote MCP URLs; `localhost`, `127.0.0.1`, and `::1` still allow HTTP.
The default is `true`. HTTP transmits credentials and request/response contents
without TLS encryption. The option also works in Python configurations and does
not change OAuth endpoint policy or HTTPS certificate verification.

The complete runnable configuration matrix is in
`examples/mcp/agent_all_modes.yaml`; the canonical guide is
`docs/advanced-guides/mcp.md`.
