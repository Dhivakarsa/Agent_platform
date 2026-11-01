"""
Documents & Sources Catalog component (Screen 2 - Tab B).
"""

from __future__ import annotations

import streamlit as st
from frontend.services.backend_client import BackendClient


def render_source_catalog(client: BackendClient) -> None:
    """Render documents and knowledge sources browsing and administrative upload."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})
    is_admin = user.get("role") == "admin"

    st.subheader("📚 Documents & Knowledge Sources")
    st.caption("Browse indexed documentation, error code guides, and API reference manuals used by the RAG Pipeline Tool.")

    # Admin: Add new source section
    if is_admin:
        with st.expander("➕ Ingest New Source (Administrator)", expanded=False):
            ingest_type = st.radio(
                "Source Type",
                ["📁 Upload Local Document (PDF, TXT, MD, CSV, JSON)", "🌐 Web Documentation URL", "📄 Text / Markdown Guide"],
                horizontal=True,
            )

            if ingest_type == "📁 Upload Local Document (PDF, TXT, MD, CSV, JSON)":
                with st.form("upload_local_file_form"):
                    uploaded_file = st.file_uploader(
                        "Choose a local document",
                        type=["pdf", "txt", "md", "csv", "json"],
                        help="Upload PDF guides, Markdown manuals, error matrices, API specs, or text files.",
                    )
                    col_f1, col_f2 = st.columns(2)
                    with col_f1:
                        doc_name = st.text_input("Document Name", placeholder="e.g. PayPal Dispute Policies 2026 (Leave blank to use filename)")
                    with col_f2:
                        doc_domain = st.selectbox("Domain", ["general", "invoicing", "payments", "disputes", "reporting"])
                    doc_desc = st.text_input("Brief Description", placeholder="e.g. Extracted from local compliance documents")

                    submitted = st.form_submit_button("📥 Upload and Index Local File", type="primary")
                    if submitted:
                        if not uploaded_file:
                            st.error("Please select a file to upload.")
                        else:
                            final_name = doc_name.strip() or uploaded_file.name
                            with st.spinner(f"Extracting, chunking, and vectorizing '{uploaded_file.name}' into Qdrant..."):
                                try:
                                    fname = uploaded_file.name.lower()
                                    if fname.endswith(".pdf"):
                                        import io
                                        import pypdf
                                        reader = pypdf.PdfReader(io.BytesIO(uploaded_file.read()))
                                        pages = [f"--- Page {i+1} ---\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages)]
                                        content = "\n\n".join(pages).strip()
                                    else:
                                        content = uploaded_file.read().decode("utf-8", errors="replace").strip()

                                    if not content:
                                        st.error("Could not extract readable text from the file.")
                                    else:
                                        client.add_source_text(
                                            token=token,
                                            name=final_name,
                                            content=content,
                                            domain=doc_domain,
                                            description=doc_desc or f"Uploaded local file: {uploaded_file.name}",
                                        )
                                        st.success(f"✅ Local document '{final_name}' successfully indexed into RAG Knowledge Base!")
                                        st.rerun()
                                except Exception as exc:
                                    st.error(f"Ingestion failed: {exc}")

            elif ingest_type == "🌐 Web Documentation URL":
                with st.form("add_url_form"):
                    col_u1, col_u2 = st.columns(2)
                    with col_u1:
                        doc_name = st.text_input("Document Name", placeholder="e.g. PayPal Webhook Security Guide")
                        doc_domain = st.selectbox("Domain", ["general", "invoicing", "payments", "disputes", "reporting"])
                    with col_u2:
                        doc_url = st.text_input("Documentation URL", placeholder="https://developer.paypal.com/docs/api/...")
                        doc_desc = st.text_input("Brief Description", placeholder="Security verification and webhook event types")

                    submitted = st.form_submit_button("📥 Ingest and Index URL", type="primary")
                    if submitted:
                        if not doc_name or not doc_url:
                            st.error("Name and URL are required.")
                        else:
                            with st.spinner("Fetching, chunking, and embedding into Qdrant knowledge_base..."):
                                try:
                                    client.add_source_url(token, doc_name, doc_url, doc_domain, doc_desc)
                                    st.success(f"✅ Source '{doc_name}' indexed successfully!")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Ingestion failed: {exc}")

            else:
                with st.form("add_text_form"):
                    col_t1, col_t2 = st.columns(2)
                    with col_t1:
                        doc_name = st.text_input("Document Name", placeholder="e.g. ISO Currency Matrix 2026")
                        doc_domain = st.selectbox("Domain", ["general", "invoicing", "payments", "disputes", "reporting"])
                    with col_t2:
                        doc_desc = st.text_input("Brief Description", placeholder="Supported decimal scales for world currencies")

                    doc_content = st.text_area("Content (Markdown / Text)", height=150, placeholder="Enter complete guide text...")
                    submitted = st.form_submit_button("📥 Ingest and Index Document", type="primary")
                    if submitted:
                        if not doc_name or not doc_content:
                            st.error("Name and Content are required.")
                        else:
                            with st.spinner("Chunking and vectorizing into Qdrant..."):
                                try:
                                    client.add_source_text(token, doc_name, doc_content, doc_domain, doc_desc)
                                    st.success(f"✅ Document '{doc_name}' indexed successfully!")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Ingestion failed: {exc}")

    st.divider()

    # Listing of sources
    with st.spinner("Loading indexed sources..."):
        try:
            sources = client.get_sources(token)
        except Exception as exc:
            st.error(f"Error fetching sources: {exc}")
            return

    st.markdown(f"**Showing {len(sources)} indexed knowledge sources:**")

    for src in sources:
        with st.container(border=True):
            col_s1, col_s2, col_s3 = st.columns([2.5, 1.5, 1])

            with col_s1:
                st.markdown(f"### 📖 {src.get('name')}")
                st.write(src.get("description", ""))
                if src.get("url"):
                    st.caption(f"🔗 [Reference Link]({src.get('url')})")

            with col_s2:
                st.markdown(f"**Domain:** `{src.get('domain')}`")
                st.markdown(f"**Source Type:** `{src.get('source_type').upper()}`")
                st.markdown(f"**Processed Chunks:** `{src.get('chunk_count')}` vectors")

            with col_s3:
                status_val = src.get("status", "indexed").upper()
                st.markdown(
                    f"<span style='background-color: #198754; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;'>{status_val}</span>",
                    unsafe_allow_html=True,
                )

                if is_admin:
                    source_id = src.get("source_id")
                    if st.button("🗑️ Delete", key=f"del_src_{source_id}"):
                        client.delete_source(token, source_id)
                        st.success("Source deleted.")
                        st.rerun()

