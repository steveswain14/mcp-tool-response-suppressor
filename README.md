![Version](https://img.shields.io/badge/version-v1.0.0-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)
![Release](https://img.shields.io/github/v/release/steveswain14/mcp-tool-response-suppressor)

\# Tool Response Suppressor (MCP)



A standalone MCP server that verifies whether an agent’s claimed tool output matches what the tool actually returned. It prevents invented or distorted tool results from entering downstream logic.



This repository contains the full implementation and runs independently. It does not depend on the full suite.



\## Usage



Add this server to your MCP client configuration (Claude Desktop, Cursor, Windsurf, or any MCP‑compatible environment):



{

&nbsp; "mcpServers": {

&nbsp;   "tool\_response\_suppressor": {

&nbsp;     "command": "python3",

&nbsp;     "args": \["/path/to/mcp-tool-response-suppressor/server.py"]

&nbsp;   }

&nbsp; }

}



\## What it does



\- Compares the agent’s claimed tool output to the real tool output  

\- Flags mismatches, omissions, and invented fields  

\- Ensures downstream logic receives only verified tool results  

\- Returns the cleaned tool response plus a list of violations  



\## Relationship to the full suite



This suppressor is also included in the consolidated mcp-hallucination-suite, which bundles all four suppressors and provides a meta-orchestrator:



https://github.com/steveswain14/mcp-hallucination-suite



