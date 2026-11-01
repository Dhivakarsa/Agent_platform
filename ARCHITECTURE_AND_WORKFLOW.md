# Datazoic Agentic Platform: System Architecture & Workflow Specification

This document provides a complete technical reference for the current system architecture, component boundaries, execution graphs, and data flows. Use this blueprint to evaluate and implement your planned architectural and workflow changes.

---

## 1. System Architecture Overview

The system is organized into a **decoupled, multi-layered architecture** separating the presentation interface, API gateway, agentic orchestration, tool discovery, and execution targets:

```mermaid
flowchart TD
    subgraph FrontendLayer["1. Presentation Layer (Streamlit :8501)"]
        UI_Auth["Login & Session Guard"]
        UI_Chat["💬 Chat & HITL Approval Interface"]
        UI_Tools["🔧 Tools & Sources (Tabs: APIs, RAG, MCP)"]
        UI_Usage["📊 Usage & Analytics (Admin / User)"]
        UI_Logs["📜 Task Logs & Audit Inspector"]
        BClient["BackendClient (HTTP/REST)"]
    end

    subgraph GatewayLayer["2. Gateway & RBAC Layer (FastAPI :8000)"]
        AuthMiddleware["JWT Auth & Security (PBKDF2)"]
        APIRouter["FastAPI Endpoints Router"]
        RBAC["Role-Based Access Control (Admin vs User)"]
    end

    subgraph AgenticLayer["3. Agent Orchestration Layer (LangGraph)"]
        PlannerNode["1. Planner Node\n(Intent decomposition & entity extraction)"]
        DiscoveryNode["2. Tool Discovery Node\n(Hybrid Qdrant + Metadata retrieval)"]
        ReasoningNode["3. Reasoning Node\n(Gemini 2.5 / 1.5 + Token tracker)"]
        ApprovalNode["4. Approval Check Node\n(Mutating tool pause / resume gate)"]
        ExecutionNode["5. Execution Node\n(Dispatch & DAG state mapping)"]
        RecoveryNode["6. Error Recovery Node\n(RAG explanation & retry logic)"]
    end

    subgraph StorageLayer["4. Storage & Retrieval Layer"]
        SQL[(Relational DB: SQLite / PostgreSQL\nUsers, Tools, Logs, Tokens, Approvals)]
        VectorDB[(Qdrant Vector Store\nTool Catalog Index & Knowledge Base)]
    end

    subgraph ExecutionLayer["5. Unified Execution Targets"]
        HttpAdapter["🌐 HTTP / REST Dispatcher\n(PayPal Sandbox APIs)"]
        McpAdapter["🔌 MCP Client Service\n(Fetch, Filesystem, SQLite, Git)"]
        MetaAdapter["⚙️ Internal Meta-Tools\n(RAG Pipeline, System Search)"]
    end

    UI_Chat --> BClient
    UI_Tools --> BClient
    UI_Usage --> BClient
    UI_Logs --> BClient
    BClient -->|"JSON over HTTP (IPv4)"| AuthMiddleware
    AuthMiddleware --> APIRouter
    APIRouter --> RBAC
    RBAC --> AgenticLayer
    
    AgenticLayer --> StorageLayer
    ExecutionNode --> HttpAdapter
    ExecutionNode --> McpAdapter
    ExecutionNode --> MetaAdapter
    HttpAdapter --> SQL
    McpAdapter --> SQL
    MetaAdapter --> SQL
```

---

## 2. Core Technological Components

| Layer | Technology | Primary Function |
| :--- | :--- | :--- |
| **Presentation** | Streamlit 1.35+ | Multi-screen responsive UI, reactive state management, JSON payload rendering. |
| **Gateway** | FastAPI + Uvicorn | Async ASGI server, JWT bearer authentication, schema validation, CORS. |
| **Orchestration** | LangGraph StateGraph | Cyclic graph execution, human-in-the-loop state pausing, DAG parameter piping. |
| **LLM Reasoning** | Google GenAI SDK | Multi-turn planning, function-call generation, usage metadata tracking. |
| **Vector DB** | Qdrant (in-memory / client) | Dense semantic search (768-dim embeddings) for tools and knowledge chunks. |
| **Relational DB** | SQLite (`aiosqlite`) / PostgreSQL | Persisted accounts, technical schemas, audit logs, and token ledgers. |
| **Protocol Layer** | Model Context Protocol (MCP) | Zero-dependency native & stdio bridge for Filesystem, Fetch, Git, and DB tools. |

---

## 3. End-to-End Workflow Processes

### Workflow 1: Natural Language Execution & Orchestration
This is the central execution path from user prompt to API execution:

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Streamlit Chat
    participant API as FastAPI Gateway
    participant LG as LangGraph Workflow
    participant Qdrant as Qdrant Vector Store
    participant Gemini as Gemini LLM
    participant Router as Execution Router
    participant Target as Target API / MCP
    participant DB as SQLite Audit Ledger

    User->>UI: Enters: "Send invoice for $50 to client@example.com"
    UI->>API: POST /chat (prompt, session_id, bearer_token)
    API->>LG: Initialize AgentState(prompt, user_id, session_id)
    
    LG->>LG: 1. Planner Node: Extract email, amount, identify sub-intents
    LG->>Qdrant: 2. Discovery Node: Dense vector search for sub-intents
    Qdrant-->>LG: Candidate tools: [paypal_create_draft_invoice, paypal_send_invoice]
    
    LG->>Gemini: 3. Reasoning Node: Prompt + Candidate Tool Schemas
    Gemini-->>LG: Function Call: paypal_create_draft_invoice(email, amount)
    
    LG->>LG: 4. Approval Check: Is tool mutating & requires human approval?
    Note over LG: paypal_create_draft_invoice is non-sensitive draft creation (proceeds)
    
    LG->>Router: 5. Execution Node: Dispatch paypal_create_draft_invoice
    Router->>Target: HTTP POST /v2/invoicing/invoices
    Target-->>Router: HTTP 201 Created {"id": "INV2-DEMO-999"}
    Router->>DB: Write ExecutionLog (request_id, tool_name, status=SUCCESS, latency)
    
    LG->>LG: Update state: extracted["invoice_id"] = "INV2-DEMO-999"
    LG->>Gemini: Next iteration: Pipe INV2-DEMO-999 into paypal_send_invoice
    Gemini-->>LG: Function Call: paypal_send_invoice(invoice_id="INV2-DEMO-999")
    
    LG->>Router: Dispatch paypal_send_invoice
    Router->>Target: HTTP POST /v2/invoicing/invoices/INV2-DEMO-999/send
    Target-->>Router: HTTP 200 OK
    Router->>DB: Write ExecutionLog
    
    LG->>LG: Synthesize final human-readable response
    LG-->>API: Return final response + execution trace + token counts
    API->>DB: Record TokenUsage (input_tokens, output_tokens, model)
    API-->>UI: JSON Response
    UI-->>User: Display formatted confirmation + expander trace
```

---

### Workflow 2: Human-in-the-Loop (HITL) Approval Workflow
When an agent selects a sensitive or mutating tool (e.g. issuing a high-value refund or deleting a resource):

```mermaid
flowchart TD
    ReasoningDone["Reasoning Node selects Tool Call"] --> CheckApproval{"ToolDefinition.approval_required == True\nOR Sensitive Mutating Tool?"}
    
    CheckApproval -->|No| ExecNode["Execution Node: Dispatch Tool"]
    CheckApproval -->|Yes| CheckStatus{"AgentState.approval_status == 'approved'?"}
    
    CheckStatus -->|Yes| ExecNode
    CheckStatus -->|No| PauseWorkflow["1. Record ApprovalRequest in DB\n2. Set state['approval_status'] = 'pending'\n3. Pause LangGraph workflow"]
    
    PauseWorkflow --> RenderCard["Streamlit Chat renders Approval Card\n(Tool Name, Inferred Arguments, Warning)"]
    RenderCard --> UserDecision{"User clicks Approve or Reject?"}
    
    UserDecision -->|Reject| Abort["POST /chat/approval (action='reject')\nWorkflow terminates with 'Action cancelled by user'"]
    UserDecision -->|Approve| Resume["POST /chat/approval (action='approve')\nWorkflow re-hydrates state and resumes execution"]
    Resume --> ExecNode
```

---

### Workflow 3: Model Context Protocol (MCP) Integration Flow
How the platform dynamically imports and executes external MCP capabilities:

```mermaid
flowchart LR
    subgraph AdminAction["Admin Discovery"]
        SelectServer["Admin selects Server\n(e.g. Filesystem / Fetch)"] --> DiscoverBtn["Click 'Discover Exposed Tools'"]
        DiscoverBtn --> Introspect["POST /mcp/servers/discover\nQueries tools/list via JSON-RPC / native"]
        Introspect --> DisplaySchemas["Preview Input Schemas & Descriptions"]
        DisplaySchemas --> SyncBtn["Click 'Sync & Index into Catalog'"]
    end

    subgraph IngestionPipeline["Catalog Synchronization"]
        SyncBtn --> Normalize["Normalize into Canonical RegisteredTool"]
        Normalize --> InsertSQL["Insert into tool_definitions (source_type='mcp')"]
        Normalize --> Embed["Generate Dense Vector Embedding"]
        Embed --> UpsertQdrant["Upsert Point into Qdrant tool_index"]
    end

    subgraph RuntimeExecution["Agent Runtime Dispatch"]
        AgentNeed["Agent reasoning selects 'mcp_read_file'"] --> ExecRouter["Execution Engine"]
        ExecRouter --> CheckSource{"tool.source_type == 'mcp'?"}
        CheckSource -->|Yes| MCPClient["MCPClientService.execute_tool()"]
        MCPClient --> NativeHandler["Native Sandboxed Handler / Stdio Process"]
        NativeHandler --> AuditLedger["Write to execution_log Table"]
    end

    IngestionPipeline --> RuntimeExecution
```

---

### Workflow 4: RAG Ingestion and Knowledge Retrieval
How technical API documentation, PDFs, and URLs are embedded and queried:

```mermaid
flowchart TD
    subgraph Ingestion["Document Ingestion"]
        DocSource["PDF Upload / Text Document / Live URL"] --> TextExtractor["Extract Clean Text & Strip Markup"]
        TextExtractor --> Chunker["Recursive Character Chunking (500 tokens, 50 overlap)"]
        Chunker --> Vectorizer["Generate 768-dim Embeddings"]
        Vectorizer --> QdrantKB["Upsert Chunks to Qdrant 'knowledge_base'"]
        Vectorizer --> SQLSource["Record Source Metadata in 'knowledge_sources'"]
    end

    subgraph Retrieval["RAG Pipeline Tool Execution"]
        UserQ["User Query: 'What is refund reason code 422?'"] --> RAGTool["rag_pipeline_tool"]
        RAGTool --> QueryEmbed["Embed Query Vector"]
        QueryEmbed --> QdrantSearch["Vector Search (Cosine Similarity Top-K)"]
        QdrantKB -.-> QdrantSearch
        QdrantSearch --> RankedPassages["Ranked Document Snippets + Sources"]
        RankedPassages --> Synthesizer["Synthesize Context Grounded Answer"]
    end
```

---

## 4. Canonical Data Model Reference

The platform relies on 7 primary database tables:

| Model Name | Table Name | Key Attributes | Purpose |
| :--- | :--- | :--- | :--- |
| **`User`** | `users` | `user_id`, `email`, `password_hash`, `role` (`admin`/`user`), `is_active` | RBAC authentication and session ownership. |
| **`ToolDefinition`** | `tool_definitions` | `tool_id`, `name`, `category`, `method`, `endpoint_path`, `source_type`, `mcp_server_name`, `request_body_schema`, `approval_required`, `status` | Canonical technical registry for all 66+ tools. |
| **`MCPServerDefinition`**| `mcp_servers` | `server_id`, `name`, `display_name`, `server_type` (`builtin`/`stdio`), `command`, `args`, `is_active` | Configured open-source and native MCP servers. |
| **`KnowledgeSource`** | `knowledge_sources` | `source_id`, `name`, `source_type` (`file`/`url`), `chunk_count`, `created_at` | Tracks uploaded RAG documentation. |
| **`ExecutionLog`** | `execution_log` | `request_id`, `session_id`, `user_id`, `tool_name`, `status`, `input_args`, `response_payload`, `latency_ms` | Immutable audit ledger powering the **Task Logs** screen. |
| **`TokenUsage`** | `token_usage` | `request_id`, `user_id`, `model`, `input_tokens`, `output_tokens`, `total_tokens` | Per-user Gemini cost and consumption accounting. |
| **`ApprovalRequest`** | `approval_requests` | `run_id`, `user_id`, `tool_name`, `arguments`, `status` (`pending`/`approved`/`rejected`) | Human-in-the-loop pause/resume control. |

---

## 5. Key Architecture & Workflow Change Points

Use this section to identify where you can modify the current system design:

### 1. Agent Orchestration (LangGraph Layer)
- **Current State**: A 6-node linear graph with cyclic reasoning loops (`planner` ➔ `tool_discovery` ➔ `reasoning` ➔ `approval_check` ➔ `execution` ➔ `recovery`).
- **Potential Changes**:
  - Add a **Multi-Agent Architecture** (e.g., specialized sub-agents: `InvoicingAgent`, `DisputeAgent`, `MCPDiagnosticsAgent`).
  - Add **Memory Checkpointing** (using SQLite or Postgres checkpoints instead of in-memory LangGraph state).
  - Replace the planner node with a full **ReAct** (Reason + Act) loop.

### 2. Tool Retrieval & Discovery
- **Current State**: Dual search (intent keywords classify domain ➔ Qdrant retrieves top-K candidates ➔ always attaches 2 meta-tools).
- **Potential Changes**:
  - Implement dynamic sparse BM25 scoring alongside dense vector search (true hybrid RRF).
  - Introduce dynamic tool hierarchy / namespaces (e.g. grouping tools under modules to scale beyond 1,000+ tools without context window overflow).

### 3. Execution Router & Adapters
- **Current State**: Single `ExecutionEngine` class that branches on `source_type` (`http_api`, `mcp`, `internal`).
- **Potential Changes**:
  - Extract into dedicated Strategy/Plugin pattern (`HttpExecutionAdapter`, `McpExecutionAdapter`, `InternalExecutionAdapter`).
  - Introduce async background job queues (e.g. Celery / Redis / BackgroundTasks) for long-running batch API operations.

### 4. Frontend & Backend Transport
- **Current State**: Synchronous HTTP POST requests from Streamlit via `BackendClient`.
- **Potential Changes**:
  - Implement Server-Sent Events (SSE) or WebSockets on `GET /chat/stream` for real-time word-by-word token streaming.
  - Decouple frontend completely (e.g. Next.js / React UI talking to the same FastAPI backend).

---

## 6. Current File System Mapping

```
application_agent/
├── app/
│   ├── main.py                     # FastAPI entry point, lifespan, routes & DI
│   ├── config.py                   # Pydantic Settings (.env loader)
│   ├── core/
│   │   └── security.py             # Salted PBKDF2 hashing & JWT tokens
│   ├── db/
│   │   ├── models.py               # SQLAlchemy ORM models
│   │   ├── session.py              # Async SQLite engine & auto-migration
│   │   └── vector_store.py         # Qdrant client (query_points + deterministic fallback)
│   ├── graph/
│   │   ├── state.py                # AgentState TypedDict
│   │   ├── nodes.py                # Planner, Discovery, Reasoning, Approval, Execution
│   │   └── workflow.py             # LangGraph StateGraph builder & runner
│   ├── meta_tools/
│   │   ├── rag_pipeline_tool.py    # Pinned Meta-Tool 1 (Documentation RAG)
│   │   └── system_search_tool.py  # Pinned Meta-Tool 2 (Capabilities & Audit Logs)
│   ├── schemas/
│   │   ├── auth.py, chat.py, mcp.py, tool.py, usage.py
│   └── services/
│       ├── execution_engine.py     # Unified Execution Router & audit logger
│       ├── mcp_client.py           # Native & Stdio MCP protocol manager
│       ├── tool_registry.py        # SQL + Qdrant tool catalog manager
│       ├── tool_retriever.py       # Two-stage intent classifier & vector search
│       └── token_usage_service.py  # User-level token accounting
├── frontend/
│   ├── app.py                      # Streamlit navigation & screen router
│   ├── components/
│   │   ├── chat.py                 # Chat UI with execution trace & HITL cards
│   │   ├── login.py                # Login screen with quick demo accounts
│   │   ├── sidebar.py              # User badge, screen radio, test scenarios
│   │   ├── tool_catalog.py         # Tab A: API Tools catalog & management
│   │   ├── source_catalog.py       # Tab B: Document RAG ingestion
│   │   ├── mcp_catalog.py          # Tab C: MCP servers & direct test workbench
│   │   ├── usage_dashboard.py      # User personal token statistics
│   │   ├── user_management.py      # Admin system-wide usage drilldown
│   │   └── task_logs.py            # Task Logs audit inspector with KPIs
│   └── services/
│       └── backend_client.py       # Frontend HTTP client (target: 127.0.0.1:8000)
├── sandbox_storage/                # Sandboxed directory for Filesystem MCP tools
├── requirements.txt                # Pinned dependencies
├── start.bat                       # Root batch launcher
└── run_application.bat             # Dual-window launcher (10s wait + health check)
```

