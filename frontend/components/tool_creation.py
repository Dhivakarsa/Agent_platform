"""
Administrator 10-Section Technical Tool Creation Form component.
"""

from __future__ import annotations

import json
import streamlit as st
from frontend.services.backend_client import BackendClient


def render_tool_creation(client: BackendClient) -> None:
    """Render the full 10-section technical tool creation form for Administrators."""
    token = st.session_state.get("token")
    st.subheader("🛠️ Register New API Tool (Administrator Configuration)")
    st.caption("Define declarative API specifications, JSON schemas, approval policies, and prerequisite dependency DAG edges.")

    with st.form("tool_create_form"):
        # Section 1: Basic Information
        st.markdown("#### 1. Basic Information")
        col_b1, col_b2 = st.columns(2)
        with col_b1:
            name = st.text_input("Tool Name (Unique snake_case)", value="paypal_custom_invoice", placeholder="e.g. paypal_create_draft_invoice")
            display_name = st.text_input("Display Name", value="Create Custom Invoice Draft")
            category = st.selectbox("Operational Category", ["invoicing", "payments", "orders", "disputes", "reporting", "subscriptions", "payouts", "general"])
        with col_b2:
            service = st.text_input("Service Namespace", value="PayPal")
            version = st.text_input("Schema Version", value="1.0.0")
            status = st.selectbox("Initial Status", ["enabled", "draft", "disabled"])

        description = st.text_area("Description (Used for semantic tool retrieval)", value="Creates a custom invoice draft for a merchant recipient.")
        tags_raw = st.text_input("Search Intent Tags / Keywords (comma separated)", value="invoice, bill, draft, merchant")

        st.divider()

        # Section 2: API Configuration
        st.markdown("#### 2. API Configuration")
        col_api1, col_api2, col_api3 = st.columns(3)
        with col_api1:
            method = st.selectbox("HTTP Method", ["POST", "GET", "PUT", "PATCH", "DELETE"])
            api_type = st.selectbox("API Type", ["REST", "GraphQL"])
        with col_api2:
            base_url = st.text_input("Base URL", value="https://api-m.sandbox.paypal.com")
            endpoint_path = st.text_input("Endpoint Path", value="/v2/invoicing/invoices")
        with col_api3:
            content_type = st.text_input("Content-Type", value="application/json")
            timeout_seconds = st.number_input("Timeout (Seconds)", value=30, min_value=5, max_value=120)
            auth_profile = st.text_input("Auth Profile", value="PAYPAL_SANDBOX")

        st.divider()

        # Section 3 & 4: Path & Query Parameters
        st.markdown("#### 3 & 4. Parameters")
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            st.markdown("**Path Parameters (JSON List):**")
            path_params_raw = st.text_area(
                "Path Params",
                value="[]",
                help='e.g. [{"name": "invoice_id", "type": "string", "required": true}]',
                height=100,
            )
        with col_p2:
            st.markdown("**Query Parameters (JSON List):**")
            query_params_raw = st.text_area(
                "Query Params",
                value="[]",
                help='e.g. [{"name": "page", "type": "integer", "required": false}]',
                height=100,
            )

        st.divider()

        # Section 5: Headers
        st.markdown("#### 5. Headers")
        headers_raw = st.text_area(
            "Custom Headers (JSON Object)",
            value='{"Prefer": "return=representation"}',
            height=80,
        )

        st.divider()

        # Section 6 & 7: Schemas
        st.markdown("#### 6 & 7. Request Body & Response JSON Schemas")
        col_sch1, col_sch2 = st.columns(2)
        with col_sch1:
            st.markdown("**Request Body Schema:**")
            body_schema_raw = st.text_area(
                "Request Body Schema",
                value='{\n  "amount": {"type": "string", "required": true},\n  "recipient_email": {"type": "string", "required": true}\n}',
                height=140,
            )
        with col_sch2:
            st.markdown("**Expected Response Schema:**")
            resp_schema_raw = st.text_area(
                "Expected Response Schema",
                value='{\n  "id": "string",\n  "status": "string"\n}',
                height=140,
            )

        st.divider()

        # Section 8: Execution Behavior & Governance
        st.markdown("#### 8. Execution Governance")
        col_gov1, col_gov2, col_gov3 = st.columns(3)
        with col_gov1:
            is_mutating = st.checkbox("Modifies Server State (Mutating)", value=True)
            approval_required = st.checkbox("Requires Human Approval Before Dispatch", value=False)
        with col_gov2:
            idempotency_supported = st.checkbox("Inject PayPal-Request-Id (Idempotency)", value=True)
            roles = st.multiselect("Permitted Roles", ["admin", "user"], default=["admin", "user"])
        with col_gov3:
            retries = st.number_input("Max Retries", value=2, min_value=0, max_value=5)

        st.divider()

        # Section 9: Dependencies DAG
        st.markdown("#### 9. Prerequisite Dependencies DAG")
        deps_raw = st.text_area(
            "Dependencies (JSON List)",
            value='[\n  {\n    "tool": "paypal_generate_invoice_number",\n    "output_field": "invoice_number",\n    "input_field": "invoice_number"\n  }\n]',
            height=100,
        )

        st.divider()

        # Section 10: Test & Save Buttons
        st.markdown("#### 10. Activation & Submission")
        submitted = st.form_submit_button("🚀 Validate & Register Tool", type="primary", use_container_width=True)

        if submitted:
            try:
                # Parse JSON fields safely
                path_params = json.loads(path_params_raw)
                query_params = json.loads(query_params_raw)
                headers = json.loads(headers_raw)
                body_schema = json.loads(body_schema_raw)
                resp_schema = json.loads(resp_schema_raw)
                dependencies = json.loads(deps_raw)
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

                payload = {
                    "name": name.strip(),
                    "display_name": display_name.strip(),
                    "description": description.strip(),
                    "category": category,
                    "service": service,
                    "tags": tags,
                    "version": version,
                    "status": status,
                    "api_type": api_type,
                    "method": method,
                    "base_url": base_url,
                    "endpoint_path": endpoint_path,
                    "content_type": content_type,
                    "timeout_seconds": int(timeout_seconds),
                    "auth_profile": auth_profile,
                    "path_params": path_params,
                    "query_params": query_params,
                    "headers": headers,
                    "request_body_schema": body_schema,
                    "response_schema": resp_schema,
                    "is_mutating": is_mutating,
                    "approval_required": approval_required,
                    "permitted_roles": roles,
                    "idempotency_supported": idempotency_supported,
                    "retry_config": {"max_retries": retries},
                    "dependencies": dependencies,
                }

                with st.spinner("Validating schemas and registering tool..."):
                    res = client.create_admin_tool(token, payload)
                    st.success(f"✅ Tool '{name}' registered, validated, and indexed in Qdrant successfully!")
                    st.rerun()

            except json.JSONDecodeError as jde:
                st.error(f"Malformed JSON in configuration fields: {jde}")
            except Exception as exc:
                st.error(f"Failed to register tool: {exc}")

