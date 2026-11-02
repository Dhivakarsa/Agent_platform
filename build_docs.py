"""
PDF documentation generator for the Datazoic Agentic Platform.

Generates the document set in ``docs/`` from this single source of truth:

1. architecture.pdf        - the scaling problem, the design, and why these choices
2. workflow.pdf            - the 10-stage pipeline, approval pause/resume, replanning
3. technical_reference.pdf - stack, data model, modules, REST API, configuration
4. user_manual.pdf         - the four screens, accounts, and sample queries
5. assignment_report.pdf   - the submission report answering the task's five pointers

Run with:  python build_docs.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Resolved from this file, so the generator works wherever the repository is cloned.
PROJECT_ROOT = Path(__file__).resolve().parent
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

CONTENT_WIDTH = letter[0] - 108


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas that computes total pages and draws headers and footers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#555555"))

        if self._pageNumber > 1:
            self.drawString(54, letter[1] - 36, "Datazoic Agentic Platform - Engineering Documentation")
            self.setStrokeColor(colors.HexColor("#CCCCCC"))
            self.setLineWidth(0.5)
            self.line(54, letter[1] - 42, letter[0] - 54, letter[1] - 42)

        self.setStrokeColor(colors.HexColor("#CCCCCC"))
        self.setLineWidth(0.5)
        self.line(54, 48, letter[0] - 54, 48)
        self.drawString(54, 36, "Datazoic Agentic Platform")
        self.drawRightString(letter[0] - 54, 36, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


def get_custom_styles():
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor("#1A365D")
    secondary_color = colors.HexColor("#2B6CB0")
    dark_text = colors.HexColor("#2D3748")

    styles.add(ParagraphStyle(
        name="DocTitle", fontName="Helvetica-Bold", fontSize=24, leading=28,
        textColor=primary_color, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="DocSubtitle", fontName="Helvetica", fontSize=12, leading=16,
        textColor=secondary_color, spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeader", fontName="Helvetica-Bold", fontSize=14, leading=18,
        textColor=primary_color, spaceBefore=14, spaceAfter=6, keepWithNext=True,
    ))
    styles.add(ParagraphStyle(
        name="SubSectionHeader", fontName="Helvetica-Bold", fontSize=11, leading=15,
        textColor=secondary_color, spaceBefore=10, spaceAfter=4, keepWithNext=True,
    ))
    styles.add(ParagraphStyle(
        name="BodyCustom", fontName="Helvetica", fontSize=9.5, leading=13.5,
        textColor=dark_text, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="BulletCustom", fontName="Helvetica", fontSize=9.5, leading=13.5,
        textColor=dark_text, leftIndent=15, firstLineIndent=-10, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="CodeBlock", fontName="Courier", fontSize=8, leading=10.5,
        textColor=colors.HexColor("#2D3748"), backColor=colors.HexColor("#F7FAFC"),
        borderColor=colors.HexColor("#E2E8F0"), borderWidth=0.5, borderPadding=6,
        spaceBefore=6, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="CalloutText", fontName="Helvetica", fontSize=9, leading=13,
        textColor=colors.HexColor("#1A365D"),
    ))
    styles.add(ParagraphStyle(
        name="TableHeader", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
        textColor=colors.white,
    ))
    styles.add(ParagraphStyle(
        name="TableCell", fontName="Helvetica", fontSize=8, leading=10.5,
        textColor=dark_text,
    ))
    return styles


STYLES = get_custom_styles()


# ── Flowable helpers ─────────────────────────────────────────────────────────


def title_block(title: str, subtitle: str) -> list:
    return [
        Paragraph(title, STYLES["DocTitle"]),
        Paragraph(subtitle, STYLES["DocSubtitle"]),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E0"), spaceAfter=12),
    ]


def heading(text: str) -> Paragraph:
    return Paragraph(text, STYLES["SectionHeader"])


def subheading(text: str) -> Paragraph:
    return Paragraph(text, STYLES["SubSectionHeader"])


def body(text: str) -> Paragraph:
    return Paragraph(text, STYLES["BodyCustom"])


def bullets(items: list[str]) -> list:
    return [Paragraph(f"• {item}", STYLES["BulletCustom"]) for item in items]


def code(text: str) -> Paragraph:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(escaped.replace("\n", "<br/>"), STYLES["CodeBlock"])


def callout(text: str, title: str = "KEY POINT") -> Table:
    t = Table([[Paragraph(f"<b>{title}</b><br/>{text}", STYLES["CalloutText"])]],
              colWidths=[CONTENT_WIDTH])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EBF8FF")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#3182CE")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def caution(text: str, title: str = "LIMITATION") -> Table:
    t = Table([[Paragraph(f"<b>{title}</b><br/>{text}", STYLES["CalloutText"])]],
              colWidths=[CONTENT_WIDTH])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFAF0")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#DD6B20")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def data_table(header: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data = [[Paragraph(h, STYLES["TableHeader"]) for h in header]]
    data += [[Paragraph(str(c), STYLES["TableCell"]) for c in row] for row in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build_pdf(filename: str, story: list) -> None:
    target = DOCS_DIR / filename
    doc = SimpleDocTemplate(
        str(target), pagesize=letter,
        leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54,
        title=filename.replace("_", " ").replace(".pdf", "").title(),
        author="Datazoic Agentic Platform",
    )
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"generated {target.relative_to(PROJECT_ROOT)}")


# =============================================================================
# 1. ARCHITECTURE
# =============================================================================


def generate_architecture_doc() -> None:
    s: list = []
    s += title_block(
        "Architecture",
        "Scaling an agent from 50 tools to 1,000+ without losing tool-selection accuracy",
    )

    s.append(heading("1. The problem"))
    s.append(body(
        "An LLM given a handful of tools picks between them reliably. The same model given "
        "hundreds degrades, in three distinct ways. <b>Tool confusion:</b> it selects a "
        "semantic neighbour, for example listing invoices when asked to send one. "
        "<b>Argument hallucination:</b> it borrows a field name from an adjacent schema, or "
        "invents an identifier it was never given. <b>Context saturation:</b> every schema in "
        "the prompt costs tokens and latency, whether or not it is relevant."
    ))
    s.append(body(
        "The failure is not the model's reasoning but the size of its choice set. So the "
        "design goal is not a better prompt; it is to make the number of registered tools "
        "irrelevant to what the model sees on any given turn."
    ))

    s.append(heading("2. The approach: bounded candidate retrieval"))
    s.append(body(
        "Tool <i>storage</i> is decoupled from tool <i>reasoning</i>. The catalog may hold any "
        "number of tools. A retrieval stage narrows it to a small shortlist, and only that "
        "shortlist is ever offered to the model."
    ))
    s.append(callout(
        "Catalog size affects the retrieval index, not the prompt. Whether 60 tools are "
        "registered or 6,000, the planner is handed roughly ten candidate schemas. The prompt "
        "stays the same size, so accuracy and cost stay flat as the catalog grows."
    ))

    s.append(subheading("Two-stage retrieval"))
    s.append(body("Implemented in <font face='Courier'>app/services/tool_retriever.py</font>:"))
    s += bullets([
        "<b>Stage 1 - domain scoping.</b> The query is classified into an operational domain "
        "(invoicing, payments, disputes, reporting, and so on) from keyword patterns. This "
        "narrows the search space before any vector work.",
        "<b>Stage 2 - hybrid search.</b> A dense vector search over Qdrant runs alongside a "
        "sparse keyword score. Dense search generalises across phrasing; sparse matching "
        "catches exact identifiers and API names that embeddings tend to blur.",
        "<b>Fusion.</b> The two rankings are merged with Reciprocal Rank Fusion, so a result "
        "ranked well by either method survives without needing a tuned score weighting.",
        "<b>Dependency expansion.</b> Prerequisite tools are pulled in alongside their "
        "dependants, so selecting <font face='Courier'>send_invoice</font> also surfaces "
        "<font face='Courier'>create_draft_invoice</font>.",
        "<b>Relaxation.</b> If domain-scoped search returns too little, the domain filter is "
        "dropped and the search repeats, so a misclassified query degrades rather than fails.",
    ])

    s.append(subheading("Dual-index storage"))
    s.append(body(
        "Each tool exists in two places, kept in sync by "
        "<font face='Courier'>app/services/tool_registry.py</font>. The relational row is the "
        "authoritative contract: endpoint, parameter schemas by location, risk tier, approval "
        "policy, permitted roles, and prerequisite edges. The vector point carries an embedded "
        "capability summary used only for discovery. Retrieval finds candidates by vector; "
        "execution and governance always read the relational contract."
    ))

    s.append(heading("3. Reasoning over the shortlist"))
    s.append(body(
        "The shortlist is translated into Gemini function declarations, one per candidate, "
        "carrying each parameter's type, description and required flag "
        "(<font face='Courier'>app/services/llm_service.py</font>). The model chooses one tool "
        "and fills its arguments. Three constraints are enforced in code rather than asked for "
        "in the prompt:"
    ))
    s += bullets([
        "<b>The shortlist is a hard boundary.</b> A returned tool name outside the candidate "
        "set is discarded before it can reach the execution engine.",
        "<b>One tool per turn.</b> The planner selects a single next action; sequences emerge "
        "from the loop, not from a plan the model commits to up front.",
        "<b>Missing arguments are asked for, not invented.</b> When a required parameter cannot "
        "be grounded in the request or in a previous step's output, the model replies with text "
        "requesting it instead of calling the tool.",
    ])
    s.append(body(
        "Answers are then synthesised from the execution trace, so every figure in a reply "
        "traces to a tool result rather than to the model's prior."
    ))

    s.append(PageBreak())

    s.append(heading("4. Framework choices and trade-offs"))
    s.append(data_table(
        ["Choice", "Why", "Trade-off accepted"],
        [
            ["LangGraph",
             "An explicit state machine with typed transitions. Each stage is a named node, so "
             "the path a turn took is inspectable, and the human-approval pause is a first-class "
             "graph feature (interrupt plus checkpointer) rather than application plumbing.",
             "More wiring than a prebuilt agent loop. Nodes and edges are declared by hand."],
            ["Not CrewAI / AutoGen",
             "Role-playing multi-agent frameworks solve collaboration between agents. The "
             "problem here is tool selection within one agent, which their abstractions do not "
             "address.",
             "No built-in multi-agent delegation if the scope later needs it."],
            ["Not a bare ReAct loop",
             "Free-form loops are hard to bound, audit, or pause mid-flight. A financial action "
             "needs a deterministic point at which approval is enforced.",
             "Less emergent flexibility; new behaviour means a new node or edge."],
            ["Qdrant",
             "Payload filtering lets domain scoping run inside the vector query, and it runs "
             "in-memory for local evaluation with no external service.",
             "In-memory mode loses the index on restart; the catalog is re-seeded."],
            ["Hybrid retrieval over pure vector search",
             "Embeddings blur near-identical API names. Sparse matching recovers exact tokens "
             "such as an endpoint or an identifier.",
             "Two searches per turn instead of one."],
            ["FastAPI + Streamlit over one process",
             "A REST boundary means authorisation and role checks are enforced server-side and "
             "the UI cannot bypass them.",
             "Two processes to run."],
        ],
        [0.17 * CONTENT_WIDTH, 0.49 * CONTENT_WIDTH, 0.34 * CONTENT_WIDTH],
    ))

    s.append(heading("5. Scaling from 50 to 500+ tools"))
    s.append(body(
        "Adding services changes the catalog, not the prompt. A new service is ingested into "
        "the registry, indexed, and assigned a namespace; retrieval then scopes to the "
        "namespaces an intent implies. The planner's input stays bounded at roughly ten "
        "candidates."
    ))
    s.append(data_table(
        ["Dimension", "Behaviour as the catalog grows"],
        [
            ["Prompt size", "Flat. Bounded by top-k, not by catalog size."],
            ["Retrieval cost", "Sub-linear. Vector search over an indexed collection, scoped by domain payload filter."],
            ["Governance", "Per-tool. Risk tier and approval policy are columns on the tool, so a new mutating endpoint inherits the approval gate automatically."],
            ["Namespacing", "Hierarchical (for example paypal.invoicing, communication.email), so cross-service collisions do not occur."],
            ["Onboarding a service", "Ingest a collection, register, index. No prompt or code change."],
        ],
        [0.22 * CONTENT_WIDTH, 0.78 * CONTENT_WIDTH],
    ))

    s.append(heading("6. Degraded operation"))
    s.append(body(
        "Every external dependency has a fallback, so the platform remains demonstrable without "
        "credentials or network access."
    ))
    s.append(data_table(
        ["Dependency", "Fallback"],
        [
            ["Gemini (reasoning)", "A deterministic rule-based planner and template responses. The pipeline, retrieval, governance and audit trail behave identically; only argument inference is weaker."],
            ["Gemini (embeddings)", "A local deterministic hash embedding, so indexing and retrieval still function offline."],
            ["Qdrant server", "In-memory client."],
            ["PostgreSQL", "SQLite via aiosqlite."],
            ["PayPal sandbox", "Mock dispatch returning realistic response shapes."],
            ["SQLite checkpointer", "In-memory checkpointer; approvals then do not survive a restart, and this is logged."],
        ],
        [0.26 * CONTENT_WIDTH, 0.74 * CONTENT_WIDTH],
    ))
    s.append(caution(
        "The fallback planner recognises only the intents encoded in its rules. It exists so "
        "the system can be evaluated without an API key, not as an equivalent substitute for "
        "the model. Configure GEMINI_API_KEY to exercise the designed path."
    ))

    build_pdf("architecture.pdf", s)


# =============================================================================
# 2. WORKFLOW
# =============================================================================


def generate_workflow_doc() -> None:
    s: list = []
    s += title_block(
        "Workflow",
        "The 10-stage pipeline, multi-step replanning, and the human-approval pause",
    )

    s.append(heading("1. The pipeline"))
    s.append(body(
        "A turn runs through an explicit LangGraph state machine "
        "(<font face='Courier'>app/graph/workflow.py</font>, nodes in "
        "<font face='Courier'>app/graph/nodes.py</font>). Every stage appends an event to a "
        "timeline that the chat screen renders, so what the UI shows is what actually ran."
    ))
    s.append(data_table(
        ["#", "Node", "Responsibility", "Next"],
        [
            ["1", "normalize_input", "Trim the query, assign request and thread identifiers, initialise the timeline", "2"],
            ["2", "analyze_intent", "Classify intent, extract entities (amounts, emails, identifiers), flag missing required fields", "3"],
            ["3", "select_registries", "Scope which namespaces the intent implies, to keep unrelated domains out of retrieval", "4"],
            ["4", "retrieve_tools", "Two-stage hybrid retrieval; returns a bounded shortlist with full parameter schemas, plus the two pinned meta-tools", "5"],
            ["5", "validate_candidates", "Candidate gate. If a mandatory field is missing, stop and ask instead of guessing", "10 or 6"],
            ["6", "plan_and_select", "Gemini function calling over the shortlist, choosing one tool and its arguments; falls back to rules when unavailable", "10 or 7"],
            ["7", "policy_and_approval_gate", "Classify risk; pause for confirmation if the action mutates external state", "10 or 8"],
            ["8", "execute_tool", "Dispatch through the execution engine with validation, idempotency header, and an audit row", "9"],
            ["9", "evaluate_result", "Decide whether the goal is met or another step is needed", "6, 7 or 10"],
            ["10", "compose_final_answer", "Synthesise the reply grounded in the execution trace; record token usage", "END"],
        ],
        [0.04 * CONTENT_WIDTH, 0.21 * CONTENT_WIDTH, 0.62 * CONTENT_WIDTH, 0.13 * CONTENT_WIDTH],
    ))

    s.append(heading("2. Multi-step work"))
    s.append(body(
        "Sequences are not planned up front. After each execution, "
        "<font face='Courier'>evaluate_result</font> returns control to the planner, which sees "
        "the results of every completed step and either selects the next tool or answers. The "
        "loop ends when the model stops calling tools, bounded by "
        "<font face='Courier'>max_iterations</font>."
    ))
    s.append(callout(
        "Because the planner sees prior results, a failed step is recoverable: it can read the "
        "error payload and retry with corrected arguments, or consult the RAG tool about an "
        "error code, rather than the turn simply failing.",
        "ERROR HANDLING",
    ))
    s.append(body(
        "Example - “Email me last month's sales volume”. Step 1 retrieves the "
        "transaction report. The planner then sees those figures and selects the email tool, "
        "populating the body from the returned values. Step 2 mutates external state, so it "
        "pauses for approval before sending."
    ))
    s.append(body(
        "Without a model configured, the rule planner uses a small set of pre-computed chains "
        "for the flows it recognises, and selects the highest-ranked candidate whose required "
        "parameters it can actually fill - so an under-specified request lists records rather "
        "than guessing an identifier."
    ))

    s.append(heading("3. The approval pause"))
    s.append(body(
        "An action is gated when any of three conditions hold: its risk tier is external-write, "
        "financial or destructive; its approval policy demands confirmation; or it is marked as "
        "mutating. The reason shown to the user names the condition that actually triggered, not "
        "a generic warning."
    ))
    s.append(subheading("Suspend and resume"))
    s.append(body(
        "The gate calls LangGraph's <font face='Courier'>interrupt()</font>, which suspends the "
        "graph <i>inside the node</i> and persists its state through the checkpointer. Approving "
        "resumes the same run from that point, so execution continues into evaluation and any "
        "remaining steps."
    ))
    s.append(code(
        "POST /chat                -> 200  status=waiting_approval, pending_approval={...}\n"
        "                             (nothing dispatched; an approval row is pending)\n"
        "POST /runs/{id}/approval  -> 200  resumes the graph with the decision\n"
        "   action=approve            execute -> evaluate -> remaining steps -> answer\n"
        "   action=reject             cancelled, no dispatch, reason recorded"
    ))
    s.append(callout(
        "Checkpoints are SQLite-backed, so a run awaiting a human survives a process restart. "
        "Each run gets its own checkpoint thread, so a suspended run cannot be disturbed by the "
        "next message in the same conversation.",
        "DURABILITY",
    ))
    s += bullets([
        "Only the run's owner, or an administrator, may decide an approval.",
        "A decision is single-use; a settled run returns HTTP 409 rather than executing again.",
        "If a checkpoint is genuinely unrecoverable, the run is marked expired and the user is "
        "asked to re-send, rather than the action being silently dispatched.",
    ])

    s.append(PageBreak())

    s.append(heading("4. Conversation memory"))
    s.append(body(
        "Recent turns of a session are replayed into each new turn's context, which is what lets "
        "a follow-up such as “send it to john@example.com” resolve what “it” "
        "refers to. History is held in the relational store rather than in graph state, so it is "
        "independent of checkpoint lifetime and visible to the Logs and Usage screens."
    ))

    s.append(heading("5. Execution and auditing"))
    s.append(body(
        "<font face='Courier'>app/services/execution_engine.py</font> is the single dispatch "
        "point for every tool, whatever its kind."
    ))
    s += bullets([
        "<b>Validation.</b> Required parameters are checked against the tool's contract before "
        "dispatch; a missing one is a validation error, not a malformed outbound call.",
        "<b>Idempotency.</b> Mutating requests carry a stable request identifier, so a retry "
        "after a timeout cannot double-charge or duplicate an invoice.",
        "<b>Routing.</b> Internal meta-tools, HTTP APIs and MCP servers are dispatched behind "
        "one interface, so the planner does not distinguish between them.",
        "<b>Audit.</b> Every attempt writes a row with the calling user, arguments, response, "
        "latency and any error - including failures, which is what makes the Logs screen "
        "trustworthy.",
    ])
    s.append(data_table(
        ["Error class", "Handling"],
        [
            ["Missing or invalid arguments", "Validation error before dispatch; returned to the planner, which can correct and retry"],
            ["HTTP error from the API", "Status and body captured in the audit row and surfaced to the planner"],
            ["Unexpected exception", "Contained, logged, and reported as a failed step; the turn continues"],
            ["Model unavailable", "Planner and synthesis fall back to deterministic paths; the turn still completes"],
            ["Iteration budget exhausted", "The loop stops and the turn answers with what it has"],
        ],
        [0.3 * CONTENT_WIDTH, 0.7 * CONTENT_WIDTH],
    ))

    s.append(heading("6. The two pinned meta-tools"))
    s.append(body(
        "Both are registered in the catalog and pinned into every shortlist regardless of "
        "retrieval score, so the agent can always consult documentation or inspect itself."
    ))
    s.append(data_table(
        ["Tool", "Purpose"],
        [
            ["rag_pipeline_tool",
             "Retrieval over the indexed knowledge base: developer documentation, error codes, "
             "currency rules, and any document an administrator uploads. Retrieved passages are "
             "summarised and cited in the answer."],
            ["system_search_tool",
             "Introspection. Scope 'capabilities' searches the tool catalog (“what tools "
             "are available for invoicing?”); scope 'execution_logs' searches run history "
             "(“what was the status of my last request?”), scoped to the calling user "
             "unless they are an administrator."],
        ],
        [0.24 * CONTENT_WIDTH, 0.76 * CONTENT_WIDTH],
    ))

    build_pdf("workflow.pdf", s)


# =============================================================================
# 3. TECHNICAL REFERENCE
# =============================================================================


def generate_technical_doc() -> None:
    s: list = []
    s += title_block("Technical Reference", "Stack, data model, modules, REST API, and configuration")

    s.append(heading("1. Stack"))
    s.append(data_table(
        ["Layer", "Technology"],
        [
            ["API", "FastAPI with Uvicorn; Pydantic v2 schemas"],
            ["Orchestration", "LangGraph compiled StateGraph with a SQLite checkpointer"],
            ["Reasoning", "Google Gemini via google-genai (function calling and synthesis)"],
            ["Vector store", "Qdrant client, 768-dimensional cosine collections (in-memory by default)"],
            ["Relational store", "SQLAlchemy 2.0 asyncio; SQLite locally, PostgreSQL supported"],
            ["Frontend", "Streamlit, communicating only through authenticated REST calls"],
            ["Auth", "PBKDF2-HMAC-SHA256 password hashing; HMAC-SHA256 signed JWTs"],
            ["Tests", "pytest with pytest-asyncio"],
        ],
        [0.22 * CONTENT_WIDTH, 0.78 * CONTENT_WIDTH],
    ))
    s.append(caution(
        "sqlalchemy[asyncio] requires greenlet, and local PDF upload requires pypdf. Both are "
        "declared in requirements.txt; installing SQLAlchemy without the asyncio extra will fail "
        "at import time."
    ))

    s.append(heading("2. Relational schema"))
    s.append(body("Defined in <font face='Courier'>app/db/models.py</font>."))
    s.append(data_table(
        ["Table", "Purpose", "Notable columns"],
        [
            ["users", "Accounts and roles", "user_id, email, password_hash, role, is_active"],
            ["tool_definitions", "The authoritative tool contract", "name, namespace, method, endpoint_path, path_params, query_params, request_body_schema, risk_level, approval_policy, permitted_roles, dependencies"],
            ["agent_runs", "One row per turn", "run_id, user_id, session_id, query, status, approval_status, tools_used, execution_trace, final_response, duration_ms"],
            ["execution_log", "One row per tool attempt", "user_id, request_id, tool_name, status, input_args, response_payload, latency_ms, error_message"],
            ["token_usage", "Model tokens per request", "request_id, user_id, model, input_tokens, output_tokens, total_tokens"],
            ["approval_requests", "Human decisions on gated actions", "approval_id, run_id, user_id, tool_name, status, reason, decided_by"],
            ["knowledge_sources", "Indexed documents", "source_id, name, source_type, domain, chunk_count, status"],
            ["mcp_servers", "Registered MCP servers", "server_id, name, server_type, command, args, discovered_tools_count"],
            ["conversations", "Conversation records", "conversation_id, user_id, title, messages"],
        ],
        [0.17 * CONTENT_WIDTH, 0.25 * CONTENT_WIDTH, 0.58 * CONTENT_WIDTH],
    ))
    s.append(callout(
        "agent_runs.run_id, token_usage.request_id and execution_log.request_id all carry the "
        "same per-turn identifier. Joining on it is what lets a task be shown with its own token "
        "cost and its own tool-call rows.",
        "JOIN KEY",
    ))

    s.append(PageBreak())

    s.append(heading("3. Module map"))
    s.append(data_table(
        ["Path", "Responsibility"],
        [
            ["app/main.py", "FastAPI application, endpoints, RBAC dependencies, startup seeding"],
            ["app/config.py", "Pydantic settings loaded from environment and .env"],
            ["app/core/security.py", "Password hashing, JWT issue and verify, role dependencies"],
            ["app/graph/workflow.py", "Graph definition, checkpointer, run and resume entry points"],
            ["app/graph/nodes.py", "The ten pipeline nodes and the deterministic fallback planner"],
            ["app/graph/state.py", "The typed AgentState carried between nodes"],
            ["app/services/llm_service.py", "Gemini function calling, grounded synthesis, token extraction"],
            ["app/services/tool_registry.py", "Dual-index catalog: relational contract plus vector index"],
            ["app/services/tool_retriever.py", "Two-stage hybrid retrieval with RRF and dependency expansion"],
            ["app/services/execution_engine.py", "Validation, idempotency, dispatch (mock / HTTP / MCP), audit"],
            ["app/services/ingestion.py", "Postman collection parsing into tool contracts"],
            ["app/services/document_service.py", "Document and URL ingestion, chunking, vector indexing"],
            ["app/services/mcp_client.py", "MCP server discovery and stdio JSON-RPC bridge"],
            ["app/meta_tools/", "The pinned RAG and System Search tools, plus general utilities"],
            ["frontend/", "Streamlit screens and the REST client"],
        ],
        [0.3 * CONTENT_WIDTH, 0.7 * CONTENT_WIDTH],
    ))

    s.append(heading("4. REST API"))
    s.append(body("All routes except <font face='Courier'>/health</font> and "
                  "<font face='Courier'>/auth/login</font> require a bearer token. Routes under "
                  "<font face='Courier'>/admin/</font> require the administrator role."))
    s.append(data_table(
        ["Method and path", "Purpose"],
        [
            ["GET /health", "Liveness probe"],
            ["POST /auth/login", "Exchange credentials for a JWT"],
            ["GET /auth/me", "Current account and role"],
            ["POST /chat", "Run one agent turn"],
            ["GET /runs/{id}", "Inspect a run (own runs, or any for an administrator)"],
            ["POST /runs/{id}/approval", "Approve or reject a suspended run; resumes the graph"],
            ["GET /tools", "Browse the catalog"],
            ["POST /admin/tools", "Register a tool"],
            ["PUT /admin/tools/{id}", "Update a tool"],
            ["PATCH /admin/tools/{id}/status", "Enable or disable a tool"],
            ["POST /admin/tools/{id}/validate", "Validate a tool's schema"],
            ["POST /admin/tools/{id}/test", "Mock-execute a tool"],
            ["GET /sources", "Browse indexed documents"],
            ["POST /admin/sources/upload", "Ingest text or an extracted document"],
            ["POST /admin/sources/url", "Ingest a documentation URL"],
            ["DELETE /admin/sources/{id}", "Remove a source"],
            ["GET /usage/me", "Own aggregate usage"],
            ["GET /usage/me/activity", "Own tasks, each with its tokens and tool-call rows"],
            ["GET /logs", "Execution log rows (own rows; all rows for an administrator)"],
            ["GET /admin/users", "User directory with aggregates"],
            ["GET /admin/users/{id}/activity", "A user's tasks with tokens and tool-call rows"],
            ["GET /admin/users/{id}/executions", "A user's run history"],
            ["GET /admin/users/{id}/tokens", "A user's token records"],
            ["GET /admin/usage/summary", "Platform totals"],
            ["GET /mcp/servers", "Registered MCP servers"],
            ["POST /mcp/servers/discover", "Discover tools a server exposes"],
            ["POST /mcp/servers/register-tools", "Index discovered MCP tools into the catalog"],
            ["POST /mcp/tools/execute", "Invoke an MCP tool directly"],
        ],
        [0.36 * CONTENT_WIDTH, 0.64 * CONTENT_WIDTH],
    ))

    s.append(PageBreak())

    s.append(heading("5. Configuration"))
    s.append(body("Copy <font face='Courier'>.env.example</font> to "
                  "<font face='Courier'>.env</font>. Defaults run the platform with no external "
                  "services."))
    s.append(data_table(
        ["Variable", "Default", "Notes"],
        [
            ["GEMINI_API_KEY", "(empty)", "Without it the deterministic planner is used and no tokens are recorded"],
            ["GEMINI_MODEL", "gemini-2.5-flash", "Model used for planning and synthesis"],
            ["EMBEDDING_MODEL", "text-embedding-004", "Falls back to a local deterministic embedding"],
            ["DATABASE_URL", "sqlite+aiosqlite:///./agentic_system.db", "PostgreSQL via asyncpg is supported"],
            ["QDRANT_USE_MEMORY", "true", "Set false to use a Qdrant server at QDRANT_HOST / QDRANT_PORT"],
            ["EXECUTION_MODE", "mock", "mock, sandbox or live; live also needs PayPal credentials"],
            ["PAYPAL_CLIENT_ID / SECRET", "(empty)", "Absent, dispatch stays in mock mode"],
            ["MAX_AGENT_ITERATIONS", "10", "Upper bound on tool calls per turn"],
            ["RETRIEVAL_TOP_K", "6", "Base shortlist size before pinning and dependency expansion"],
            ["JWT_SECRET_KEY", "dev default", "Must be replaced outside local development"],
            ["SMTP_*", "(empty)", "Absent, email dispatch is simulated"],
        ],
        [0.25 * CONTENT_WIDTH, 0.27 * CONTENT_WIDTH, 0.48 * CONTENT_WIDTH],
    ))

    s.append(heading("6. Tests"))
    s.append(body("Run <font face='Courier'>python -m pytest tests/ -q</font>. The suite runs "
                  "offline: no API key, no network, no external services."))
    s.append(data_table(
        ["Suite", "Covers"],
        [
            ["tests/unit/test_llm_planner.py", "Schema translation to function declarations, shortlist enforcement, token extraction, fallback on model failure, graph wiring (Gemini stubbed)"],
            ["tests/integration/test_approval_resume.py", "Suspend and resume, rejection, single-use decisions, survival across a restart, conversation memory"],
            ["tests/integration/test_paypal_flows.py", "End-to-end turns for the task's scenarios"],
            ["tests/unit/", "Auth, permissions, retrieval, ingestion, meta-tools"],
        ],
        [0.34 * CONTENT_WIDTH, 0.66 * CONTENT_WIDTH],
    ))

    s.append(heading("7. Observability"))
    s.append(body(
        "Every turn emits a stage-by-stage timeline, rendered in the chat screen, alongside the "
        "audit tables. LangSmith tracing is wired in and optional: with "
        "<font face='Courier'>LANGSMITH_API_KEY</font> set, LangGraph contributes a span per "
        "pipeline node and <font face='Courier'>app/core/tracing.py</font> adds explicit spans "
        "around the two reasoning calls, carrying the prompt, the candidate shortlist, the "
        "selected tool and the token counts. With no key, tracing is off and the decorator is a "
        "no-op, so nothing in the call path depends on it."
    ))

    s.append(heading("8. Known limitations"))
    s += bullets([
        "<b>The live model path is not covered by automated tests.</b> Planner and synthesis are "
        "tested against a stubbed SDK; running with a real key is a manual step.",
        "<b>Embeddings fall back to a local hash with no API key.</b> Document retrieval then "
        "relies on token overlap and will not match synonyms. With a key configured, both the "
        "tool index and the knowledge base use Gemini embeddings.",
        "<b>Changing embedding provider requires a reindex.</b> Content indexed with one "
        "provider will not match queries embedded by the other; affected sources must be "
        "deleted and re-ingested.",
        "<b>Qdrant runs in memory by default</b>, so the tool index is rebuilt on each start.",
        "<b>13 of 73 catalog entries carry no parameters</b>, being endpoints the seeded "
        "collection defines without path, query or body fields.",
        "<b>The deterministic fallback router recognises only its encoded intents</b>, so a "
        "document question with no trigger word can miss the RAG tool. The model-driven planner "
        "routes on meaning instead.",
        "<b>Schema changes use ad-hoc SQLite ALTER statements</b> rather than a migration tool.",
    ])

    build_pdf("technical_reference.pdf", s)


# =============================================================================
# 4. USER MANUAL
# =============================================================================


def generate_usermanual_doc() -> None:
    s: list = []
    s += title_block("User Manual", "Running the platform, and what each screen does")

    s.append(heading("1. Starting up"))
    s.append(body("On Windows, <font face='Courier'>run_application.bat</font> creates the "
                  "virtual environment, installs dependencies, and starts both processes. "
                  "Manually:"))
    s.append(code(
        "python -m venv .venv\n"
        ".venv\\Scripts\\activate          # Windows\n"
        "source .venv/bin/activate        # macOS / Linux\n"
        "pip install -r requirements.txt\n"
        "cp .env.example .env\n"
        "\n"
        "uvicorn app.main:app --port 8000 --reload     # terminal 1\n"
        "streamlit run frontend/app.py                 # terminal 2"
    ))
    s += bullets([
        "Web portal: <font face='Courier'>http://localhost:8501</font>",
        "API and interactive docs: <font face='Courier'>http://127.0.0.1:8000/docs</font>",
        "Health probe: <font face='Courier'>http://127.0.0.1:8000/health</font>",
    ])
    s.append(body(
        "On first start the database is created, the demo accounts are seeded, and the catalog "
        "is populated with the PayPal collection, the general-purpose tools, the two pinned "
        "meta-tools, and the built-in MCP tools."
    ))

    s.append(heading("2. Accounts"))
    s.append(data_table(
        ["Role", "Email", "Password", "Access"],
        [
            ["Administrator", "admin@examplemail.com", "admin@123",
             "Everything: chat, tool creation, document ingestion, and every user's activity"],
            ["Standard user", "user@examplemail.com", "user@123",
             "Chat, browse active tools and documents, and only their own activity"],
        ],
        [0.16 * CONTENT_WIDTH, 0.25 * CONTENT_WIDTH, 0.14 * CONTENT_WIDTH, 0.45 * CONTENT_WIDTH],
    ))
    s.append(caution(
        "These are seeded development credentials. Change them, and JWT_SECRET_KEY, before "
        "exposing the platform to anyone else."
    ))

    s.append(heading("3. Screen 1 - Chat"))
    s.append(body(
        "Ask in plain language. The pipeline panel lists the stages that actually ran for your "
        "turn; expanding a reply shows the tools executed, the tokens consumed, and the full "
        "execution trace with arguments and responses."
    ))
    s.append(data_table(
        ["Try", "What happens"],
        [
            ["What tools are available for managing invoices?",
             "System Search returns matching catalog entries"],
            ["What are the valid refund reason codes?",
             "RAG retrieves from the knowledge base and cites the document"],
            ["What was my total sales volume last month?",
             "Retrieves the transaction report and reports the returned figures"],
            ["Is there a dispute open from user_123?",
             "Lists disputes, since no specific dispute identifier was supplied"],
            ["Send an invoice for $50 to client@example.com",
             "Pauses for confirmation, because sending mutates external state"],
            ["What is the status of my last request?",
             "System Search returns your own recent executions"],
        ],
        [0.44 * CONTENT_WIDTH, 0.56 * CONTENT_WIDTH],
    ))
    s.append(subheading("Confirming an action"))
    s.append(body(
        "When an action changes external state the run pauses and shows the tool, the reason, "
        "and the exact arguments that would be sent. <b>Approve</b> resumes the run and "
        "continues through any remaining steps; <b>Reject</b> cancels it and nothing is "
        "dispatched. A decision can be made once."
    ))

    s.append(PageBreak())

    s.append(heading("4. Screen 2 - Tools and Sources"))
    s.append(subheading("Registered tools"))
    s.append(body(
        "Browse and filter the catalog by category, method and status. Administrators can "
        "register a tool through a form covering basic information, API configuration, path, "
        "query and header parameters, request and response schemas, execution behaviour "
        "(mutating, approval required, permitted roles, retries), prerequisite dependencies, "
        "and schema validation with a mock test run. A registered tool is written to both the "
        "relational store and the vector index, so it becomes discoverable immediately."
    ))
    s.append(subheading("Documents and knowledge base"))
    s.append(body(
        "Administrators can ingest a local file (PDF, TXT, MD, CSV, JSON), a documentation URL, "
        "or pasted text. Content is extracted, chunked with overlap, embedded and indexed with "
        "metadata, after which the RAG tool can answer from it. The listing shows each source's "
        "domain, type and chunk count."
    ))
    s.append(subheading("MCP servers"))
    s.append(body(
        "Discover the tools an MCP server exposes and index them into the catalog, after which "
        "they are retrieved and executed like any other tool."
    ))

    s.append(heading("5. Screen 3 - Usage"))
    s.append(body(
        "A standard user sees their own totals and a table of their tasks: what was asked, "
        "whether it succeeded, how many tools ran, the prompt, output and total tokens for that "
        "task, and its latency. An administrator sees platform totals and a user directory; "
        "expanding a user shows the same per-task table for that account, plus the tools they "
        "have dispatched."
    ))
    s.append(callout(
        "Token counts are the figures the model SDK reported. With no API key configured no "
        "model runs, so the totals are legitimately zero rather than estimated.",
        "ON TOKEN COUNTS",
    ))

    s.append(heading("6. Screen 4 - Logs"))
    s.append(body(
        "A standard user sees only their own tasks. An administrator picks a user and expands "
        "them to see that user's tasks, each with its tool calls in a table: tool, description "
        "or error, status, latency and timestamp. Selecting a call shows the input arguments and "
        "the response payload. Failed calls are included, which is the point - the log is an "
        "audit trail, not a success feed."
    ))

    s.append(heading("7. Troubleshooting"))
    s.append(data_table(
        ["Symptom", "Cause and fix"],
        [
            ["ImportError about greenlet on startup",
             "SQLAlchemy was installed without the asyncio extra. Reinstall from requirements.txt."],
            ["Token totals are zero",
             "No GEMINI_API_KEY is configured, so no model ran. Expected; set the key to use the model."],
            ["Replies look templated and terse",
             "The deterministic fallback planner is active, for the same reason."],
            ["Local PDF upload fails",
             "pypdf is missing. Reinstall from requirements.txt."],
            ["Frontend shows a connection error",
             "The backend is not running, or is on a different port than the client expects."],
            ["A run cannot be approved and returns 409",
             "It was already decided, or its checkpoint is gone after a restart in in-memory mode. Re-send the request."],
            ["The catalog looks empty",
             "Seeding runs on first start; check the backend log for the auto-seed line."],
        ],
        [0.33 * CONTENT_WIDTH, 0.67 * CONTENT_WIDTH],
    ))

    build_pdf("user_manual.pdf", s)


# =============================================================================
# 5. ASSIGNMENT REPORT
# =============================================================================


def generate_assignment_report() -> None:
    s: list = []
    s += title_block(
        "Design a Scalable Agentic System",
        "Submission report - Datazoic AI Engineer task",
    )

    s.append(body(
        "<b>Scope.</b> A working platform that lets a user drive 50+ PayPal APIs by chat, plus a "
        "RAG tool and a system-introspection tool, designed so that the number of registered "
        "tools is not a constraint on accuracy. Architecture, workflow, technical reference and "
        "user manual accompany this report."
    ))

    s.append(heading("1. The problem"))
    s.append(body(
        "Tool-calling accuracy degrades as the tool count grows: the model picks semantic "
        "neighbours, borrows parameters across schemas, and pays for a context window full of "
        "irrelevant definitions. The constraint is the size of the choice set, so the design "
        "removes catalog size from the model's view of a turn."
    ))

    s.append(heading("2. Agent structure"))
    s.append(body(
        "A single agent as an explicit LangGraph state machine of ten named stages, rather than "
        "a crew of role-playing agents or a free-form ReAct loop. The problem is tool selection "
        "within one agent, which multi-agent abstractions do not address; and a financial action "
        "needs a deterministic point at which approval is enforced, which a free-form loop makes "
        "hard to guarantee. The cost is more explicit wiring."
    ))

    s.append(heading("3. Tool selection and routing"))
    s.append(body(
        "Retrieval narrows the catalog before the model is involved: domain classification, then "
        "dense vector search alongside sparse keyword matching, fused with Reciprocal Rank "
        "Fusion, then prerequisite expansion. The resulting shortlist of roughly ten tools is "
        "translated into function declarations carrying each parameter's type and required flag. "
        "The model picks one tool and fills its arguments; a name outside the shortlist is "
        "rejected in code, and a required argument that cannot be grounded produces a question "
        "to the user rather than an invented value."
    ))
    s.append(callout(
        "This is the core of the answer. The catalog can grow without bound because what reaches "
        "the model is bounded by top-k, not by the number of registered tools."
    ))

    s.append(heading("4. State management"))
    s.append(body(
        "A typed state object carries identifiers, intent, entities, the candidate shortlist, the "
        "plan, approval status, the execution trace, token accounting and the timeline between "
        "stages. Two concerns are separated deliberately: LangGraph checkpoints, scoped per run, "
        "exist so a run suspended for human approval can be resumed - durably, surviving a "
        "restart; conversation history lives in the relational store and is replayed into each "
        "new turn, so it is independent of checkpoint lifetime and visible to the reporting "
        "screens. Scoping checkpoints per run also means a suspended run cannot be corrupted by "
        "the next message in the same conversation."
    ))

    s.append(heading("5. Scalability"))
    s.append(data_table(
        ["Concern", "How it scales"],
        [
            ["Prompt size", "Flat in catalog size; bounded by top-k"],
            ["Adding a service", "Ingest, register, index. No prompt or code change"],
            ["Collisions across services", "Hierarchical namespaces, scoped per intent"],
            ["Governance of new endpoints", "Risk tier and approval policy are per-tool columns, so a new mutating endpoint inherits the gate"],
            ["Retrieval latency", "Indexed vector search with a domain payload filter"],
        ],
        [0.28 * CONTENT_WIDTH, 0.72 * CONTENT_WIDTH],
    ))

    s.append(heading("6. Error handling"))
    s += bullets([
        "<b>Before dispatch:</b> required arguments are validated against the tool contract; a "
        "missing mandatory field stops the turn with a question instead of a guess.",
        "<b>At dispatch:</b> mutating calls carry a stable idempotency identifier, so a retry "
        "after a timeout cannot double-charge.",
        "<b>After a failure:</b> the error payload returns to the planner, which can retry with "
        "corrected arguments or consult the RAG tool about the error code.",
        "<b>Dependency failure:</b> every external dependency has a fallback, so the platform "
        "runs with no key, no network and no external services.",
        "<b>Bounded loops:</b> an iteration budget caps tool calls per turn.",
        "<b>Audit:</b> failures are recorded with the same fidelity as successes.",
    ])

    s.append(PageBreak())

    s.append(heading("7. Observability"))
    s.append(body(
        "Each turn emits a stage-by-stage timeline, surfaced in the chat screen, alongside "
        "relational audit tables: one row per run, one per tool attempt, one per token record, "
        "one per approval decision. Joining them on the per-turn request identifier is what lets "
        "a task be reported with its own token cost and its own tool calls."
    ))
    s.append(body(
        "<b>LangSmith</b> is wired in and optional, as the task suggested. With a key configured, "
        "LangGraph contributes a span per pipeline node and the two reasoning calls are traced "
        "explicitly, carrying the prompt, the candidate shortlist, the selected tool and the "
        "token counts. With no key, tracing is off and the decorator is a no-op, so no part of "
        "the call path depends on it being installed or reachable."
    ))

    s.append(heading("8. What runs today"))
    s.append(data_table(
        ["Capability", "Status"],
        [
            ["Chat-driven orchestration over the catalog", "Working"],
            ["Two-stage hybrid retrieval with RRF and dependency expansion", "Working"],
            ["Gemini function calling over the bounded shortlist", "Working; not covered by automated tests"],
            ["Grounded answer synthesis from the execution trace", "Working; not covered by automated tests"],
            ["Model-driven multi-step replanning", "Working; not covered by automated tests"],
            ["Deterministic fallback with no API key", "Working and tested"],
            ["Human approval with durable suspend and resume", "Working and tested"],
            ["RAG tool over an extensible knowledge base", "Working; Gemini embeddings when a key is set, local fallback otherwise"],
            ["System Search over catalog and logs", "Working and tested"],
            ["Role-based access control and per-user isolation", "Working and tested"],
            ["Token accounting from SDK-reported counts", "Working"],
            ["MCP integration", "Working"],
            ["LangSmith tracing (optional, key-gated)", "Working and tested"],
            ["Knowledge-base deletion removes its vectors", "Working and tested"],
        ],
        [0.55 * CONTENT_WIDTH, 0.45 * CONTENT_WIDTH],
    ))
    s.append(body(
        "The automated suite runs offline and covers the pipeline, retrieval, governance, "
        "approval lifecycle, RBAC and the planner's contract against a stubbed SDK. The live "
        "model path is exercised manually by configuring a key; it is called out as untested "
        "rather than claimed as verified."
    ))

    s.append(heading("9. What I would do next"))
    s += bullets([
        "<b>Evaluate retrieval directly.</b> A labelled set of query-to-tool pairs, measuring "
        "whether the correct tool appears in the shortlist, separates retrieval quality from "
        "model quality - the number that matters most as the catalog grows.",
        "<b>Re-rank the shortlist</b> with a cross-encoder when a domain holds many near-identical "
        "endpoints.",
        "<b>Add a reindex command</b>, so switching embedding provider does not require deleting "
        "and re-ingesting each source by hand.",
        "<b>Broaden the fallback router</b>, or drop it once a key is always available, so "
        "document questions route on meaning rather than trigger words.",
        "<b>Replace ad-hoc SQLite column migration with a migration tool</b> before any "
        "deployment that must preserve data.",
    ])

    build_pdf("assignment_report.pdf", s)


# =============================================================================


def main() -> None:
    generate_architecture_doc()
    generate_workflow_doc()
    generate_technical_doc()
    generate_usermanual_doc()
    generate_assignment_report()
    print(f"\n5 documents written to {DOCS_DIR.relative_to(PROJECT_ROOT)}/")


if __name__ == "__main__":
    main()
