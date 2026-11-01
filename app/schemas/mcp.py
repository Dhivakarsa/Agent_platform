"""
Pydantic schemas for Model Context Protocol (MCP) server management and tool discovery.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    """Configuration for an MCP server (built-in, stdio, or sse)."""
    name: str = Field(..., description="Unique slug name of the MCP server, e.g. 'fetch', 'filesystem'")
    display_name: str = Field(..., description="Human readable display name")
    description: str = Field(default="", description="Description of capabilities provided")
    server_type: Literal["builtin", "stdio", "sse"] = "builtin"
    command: Optional[str] = Field(default=None, description="Command executable for stdio servers, e.g. 'npx', 'uvx', 'python'")
    args: List[str] = Field(default_factory=list, description="Command line arguments")
    env_vars: Dict[str, str] = Field(default_factory=dict, description="Environment variables passed to the server")
    is_active: bool = Field(default=True, description="Whether server is active")
    discovered_tools_count: int = Field(default=0, description="Number of discovered tools")


class MCPDiscoveredTool(BaseModel):
    """Normalized tool specification retrieved from an MCP server."""
    name: str = Field(..., description="Unique name within the platform, e.g. 'mcp_filesystem_read_file'")
    remote_tool_name: str = Field(..., description="Original tool name on the MCP server, e.g. 'read_file'")
    server_name: str = Field(..., description="Source MCP server name")
    display_name: str = Field(default="")
    description: str = Field(default="")
    category: str = Field(default="mcp_tools")
    is_mutating: bool = Field(default=False)
    approval_required: bool = Field(default=False)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    parameters: List[Dict[str, Any]] = Field(default_factory=list)
    is_already_registered: bool = Field(default=False)


class MCPDiscoverResponse(BaseModel):
    """Response returned when querying an MCP server's exposed tools."""
    server_name: str
    server_type: str
    status: str = "connected"
    tools_count: int
    tools: List[MCPDiscoveredTool]


class MCPRegisterToolsRequest(BaseModel):
    """Request to import selected MCP tools into the platform's tool registry."""
    server_name: str = Field(..., description="Name of the MCP server")
    tool_names: List[str] = Field(default_factory=list, description="List of tool names to register (empty = register all discovered)")


class MCPRegisterToolsResponse(BaseModel):
    """Response after importing MCP tools."""
    server_name: str
    registered_count: int
    registered_tools: List[str]
    message: str


class MCPToolExecuteRequest(BaseModel):
    """Direct execution request for testing an MCP tool."""
    server_name: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

