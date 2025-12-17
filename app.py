import streamlit as st
import tempfile
import os
import sys
import time
from jamaibase import JamAI, protocol as p  # keep teammate's import (protocol unused but preserved)

# -----------------------
# Page / UI configuration
# -----------------------
st.set_page_config(page_title="AERN | AI Emergency Response Navigator", page_icon="🚨", layout="wide")

st.markdown("""
<style>
    .stButton>button {height: 3em; width: 100%; border-radius: 10px; font-weight: bold; font-size: 20px;} 
    .stChatMessage {border-radius: 15px; padding: 10px;}
</style>
""", unsafe_allow_html=True)

# -----------------------
# Secrets / Credentials
# -----------------------
# Support both naming schemes:
# - New teammate scheme: st.secrets["JAMAI_API_KEY"], st.secrets["PROJECT_ID"], st.secrets["TABLE_ID"]
# - Existing scheme: JAMAI_PROJECT_ID / JAMAI_PAT_KEY or environment variables JAMAI_PROJECT_ID / JAMAI_PAT_KEY
def _load_secrets():
    # prefer streamlit secrets when present
    proj = None
    pat = None
    table_text = None
    table_audio = None
    table_photo = None
    table_multi = None
    table_chat = None

    if hasattr(st, "secrets") and isinstance(st.secrets, dict) and st.secrets:
        # teammate keys
        pat = st.secrets.get("JAMAI_API_KEY") or st.secrets.get("JAMAI_PAT_KEY")
        proj = st.secrets.get("PROJECT_ID") or st.secrets.get("JAMAI_PROJECT_ID")
        # optional per-table ids
        table_text = st.secrets.get("TABLE_TEXT_ID") or st.secrets.get("TABLE_ID_TEXT") or st.secrets.get("TABLE_ID")
        table_audio = st.secrets.get("TABLE_AUDIO_ID") or st.secrets.get("TABLE_ID_AUDIO")
        table_photo = st.secrets.get("TABLE_PHOTO_ID") or st.secrets.get("TABLE_ID_PHOTO")
        table_multi = st.secrets.get("TABLE_MULTI_ID") or st.secrets.get("TABLE_ID_MULTI")
        table_chat = st.secrets.get("TABLE_CHAT_ID") or st.secrets.get("TABLE_ID_CHAT") or st.secrets.get("TABLE_ID")
    # fallback to environment
    if not proj:
        proj = os.getenv("JAMAI_PROJECT_ID") or os.getenv("PROJECT_ID")
    if not pat:
        pat = os.getenv("JAMAI_PAT_KEY") or os.getenv("JAMAI_API_KEY") or os.getenv("JAMAI_PAT")
    # fallback table ids (hard-coded defaults; update to your actual table IDs)
    table_text = table_text or os.getenv("TABLE_ID_TEXT") or "text_received"
    table_audio = table_audio or os.getenv("TABLE_ID_AUDIO") or "audio_receive"
    table_photo = table_photo or os.getenv("TABLE_ID_PHOTO") or "picture_receipt"
    table_multi = table_multi or os.getenv("TABLE_ID_MULTI") or "combined"
    table_chat = table_chat or os.getenv("TABLE_ID_CHAT") or table_multi

    return proj.strip() if isinstance(proj, str) else proj, (pat.strip() if isinstance(pat, str) else pat), {
        "text": table_text, "audio": table_audio, "photo": table_photo, "multi": table_multi, "chat": table_chat
    }

PROJECT_ID, PAT_KEY, TABLE_IDS = _load_secrets()

# show connection status
if PROJECT_ID and PAT_KEY:
    st.sidebar.success("✅ JamAI credentials loaded")
else:
    st.sidebar.warning("⚠️ JamAI credentials missing. Set secrets or environment variables.")

# -----------------------
# Initialize JamAI client
# -----------------------
jamai = None
if PROJECT_ID and PAT_KEY:
    try:
        jamai = JamAI(token=PAT_KEY, project_id=PROJECT_ID)
    except Exception as e:
        st.sidebar.error(f"Failed to initialize JamAI client: {e}")
        jamai = None

# -----------------------
# Helpers (file save, upload, send)
# -----------------------
def save_uploaded_file(uploaded_file):
    try:
        suffix = f".{uploaded_file.name.split('.')[-1]}" if "." in uploaded_file.name else ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            return tmp_file.name
    except Exception as e:
        st.error(f"Error saving uploaded file: {e}")
        return None

def _get_uri_from_upload(upload_resp):
    if upload_resp is None:
        return None
    if isinstance(upload_resp, dict):
        return upload_resp.get("uri") or upload_resp.get("url")
    if hasattr(upload_resp, "uri"):
        return getattr(upload_resp, "uri", None)
    if hasattr(upload_resp, "url"):
        return getattr(upload_resp, "url", None)
    if hasattr(upload_resp, "row") and isinstance(upload_resp.row, dict):
        return upload_resp.row.get("uri") or upload_resp.row.get("url")
    return None

def _normalize_row_dict(d):
    if not isinstance(d, dict):
        return {}
    for key in ("values", "fields", "data"):
        if key in d and isinstance(d[key], dict):
            return d[key]
    return d

def _find_row_dict(response):
    if response is None:
        return {}
    if isinstance(response, list) and response:
        candidate = response[0]
        if isinstance(candidate, dict):
            return _normalize_row_dict(candidate)
    if isinstance(response, dict):
        if "row" in response and isinstance(response["row"], dict):
            return _normalize_row_dict(response["row"])
        if "rows" in response and isinstance(response["rows"], list) and response["rows"]:
            return _normalize_row_dict(response["rows"][0])
        if "values" in response and isinstance(response["values"], dict):
            return _normalize_row_dict(response["values"])
        if "data" in response and isinstance(response["data"], dict):
            return _normalize_row_dict(response["data"])
        return _normalize_row_dict(response)
    if hasattr(response, "row"):
        try:
            r = getattr(response, "row")
            if isinstance(r, dict):
                return _normalize_row_dict(r)
        except Exception:
            pass
    if hasattr(response, "rows"):
        try:
            rlist = getattr(response, "rows")
            if isinstance(rlist, list) and rlist:
                return _normalize_row_dict(rlist[0])
        except Exception:
            pass
    if hasattr(response, "__dict__"):
        d = getattr(response, "__dict__", {})
        return _find_row_dict(d)
    return {}

def _extract_field_safe(row_dict, key, default=None):
    if not isinstance(row_dict, dict):
        return default
    if key in row_dict:
        return row_dict.get(key)
    # search nested
    def search(obj):
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                res = search(v)
                if res is not None:
                    return res
        if isinstance(obj, list):
            for item in obj:
                res = search(item)
                if res is not None:
                    return res
        return None
    found = search(row_dict)
    return found if found is not None else default

def _cleanup_temp(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

def send_table_row(table_id, data, stream=False):
    """
    Use jamai.table.add_table_rows when available (rows as list).
    Returns SDK response or raises informative error.
    """
    if jamai is None:
        raise RuntimeError("JamAI client not initialized.")
    table_obj = getattr(jamai, "table", None)
    if table_obj is None:
        raise AttributeError("jamai.table is not present on the JamAI client instance.")

    if hasattr(table_obj, "add_table_rows") and callable(getattr(table_obj, "add_table_rows")):
        try:
            return table_obj.add_table_rows(table_id=table_id, rows=[data])
        except TypeError:
            return table_obj.add_table_rows(table_id, [data])
        except Exception as e:
            raise RuntimeError(f"jamai.table.add_table_rows raised an error: {e}") from e

    # fallback to update_table_rows or add_table_rows-like ops
    if hasattr(table_obj, "add_table_rows") is False and hasattr(table_obj, "add_table_row"):
        try:
            f = getattr(table_obj, "add_table_row")
            return f(table_id=table_id, row=data)
        except Exception as e:
            raise RuntimeError(f"jamai.table.add_table_row raised an error: {e}") from e

    available = sorted(dir(table_obj))
    raise AttributeError(f"Could not find an API to insert rows on jamai.table. Available attrs: {available}")

# -----------------------
# Main UI: Tabs (merged)
# -----------------------
st.title("🚨 AERN")
st.caption("AI Emergency Response Navigator")

# Create top-level tabs:
tab_panic, tab_single, tab_multi, tab_chat = st.tabs(["🔥 PANIC MODE", "Single Modality Analysis", "Multi-Modality Fusion", "💬 AI Assistant"])

# === PANIC MODE (teammate features) ===
with tab_panic:
    st.header("Panic Mode — quick actions")
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        if st.button("🌊 FLOOD "):
            st.error("⚠️ FLOOD ALERT! 1. Turn off power. 2. Move to high ground.")
    with col_p2:
        if st.button("🔥 FIRE "):
            st.error("⚠️ FIRE ALERT! 1. Stay low. 2. Do not use elevators.")
    with col_p3:
        if st.button("🚑 MEDICAL "):
            st.error("⚠️ MEDICAL ALERT! Calling emergency contact...")

# === Single Modality Analysis (original features) ===
with tab_single:
    st.header("Single Input Analysis (3 Dedicated Tables)")
    st.info("Input will be sent to the table matching the input type.")

    input_type = st.radio("Select Input Type", ["Text", "Audio", "Photo"], horizontal=True)

    user_data = {}
    table_id_to_use = None
    ready_to_send = False

    if input_type == "Text":
        text_input = st.text_area("Describe the emergency situation:")
        if text_input:
            user_data = {"text": text_input}
            table_id_to_use = TABLE_IDS["text"]
            ready_to_send = True

    elif input_type == "Audio":
        audio_file = st.file_uploader("Upload Audio Recording", type=["mp3", "wav", "m4a"])
        if audio_file:
            temp_path = save_uploaded_file(audio_file)
            if temp_path:
                with st.spinner("Uploading audio..."):
                    try:
                        upload_resp = jamai.file.upload_file(temp_path) if jamai else None
                        uploaded_uri = _get_uri_from_upload(upload_resp)
                        if not uploaded_uri:
                            st.error("Upload succeeded but no URI was returned.")
                        else:
                            user_data = {"audio": uploaded_uri}
                            table_id_to_use = TABLE_IDS["audio"]
                            ready_to_send = True
                    except Exception as e:
                        st.error(f"Audio upload failed: {e}")
                    finally:
                        _cleanup_temp(temp_path)

    elif input_type == "Photo":
        photo_file = st.file_uploader("Upload Scene Photo", type=["jpg", "png", "jpeg"])
        if photo_file:
            st.image(photo_file, caption="Preview", width=300)
            temp_path = save_uploaded_file(photo_file)
            if temp_path:
                with st.spinner("Uploading photo..."):
                    try:
                        upload_resp = jamai.file.upload_file(temp_path) if jamai else None
                        uploaded_uri = _get_uri_from_upload(upload_resp)
                        if not uploaded_uri:
                            st.error("Upload succeeded but no URI was returned.")
                        else:
                            user_data = {"photo": uploaded_uri}
                            table_id_to_use = TABLE_IDS["photo"]
                            ready_to_send = True
                    except Exception as e:
                        st.error(f"Photo upload failed: {e}")
                    finally:
                        _cleanup_temp(temp_path)

    if st.button("Analyze Single Input", disabled=not ready_to_send):
        with st.spinner(f"Consulting AERN Brain via table: {table_id_to_use}..."):
            try:
                if jamai:
                    response = send_table_row(table_id=table_id_to_use, data=user_data, stream=False)
                else:
                    response = {"row": {"description": "Simulated description (offline)", "summary": "Simulated summary"}}  # fallback for offline
                with st.expander("Raw response from JamAI"):
                    st.write(response)
                row = _find_row_dict(response)
                desc = _extract_field_safe(row, "description", default="No description generated")
                summary = _extract_field_safe(row, "summary", default="No summary generated")
                st.subheader("📋 Situation Description")
                st.write(desc)
                st.divider()
                st.subheader("📢 Action Summary")
                st.success(summary)
            except Exception as e:
                st.error(f"An error occurred: {e}")
                st.write("Check Table IDs and column names. If needed, expand the JamAI debug info in the sidebar.")

# === Multi-Modality Fusion (original features) ===
with tab_multi:
    st.header("Multi-Modality Fusion")
    st.info(f"Connected to Table: `{TABLE_IDS['multi']}` (One table handles multiple inputs)")

    col1, col2 = st.columns(2)
    with col1:
        multi_text = st.text_area("Text Input", height=150)
        multi_audio = st.file_uploader("Audio Input", type=["mp3", "wav", "m4a"], key="m_audio")
    with col2:
        multi_photo = st.file_uploader("Photo Input", type=["jpg", "png", "jpeg"], key="m_photo")
        if multi_photo:
            st.image(multi_photo, width=200)

    if st.button("Analyze Combined Data"):
        if not (multi_text or multi_audio or multi_photo):
            st.error("Please provide at least one input.")
        else:
            with st.spinner("Processing multi-modal emergency data..."):
                try:
                    multi_data = {}
                    if multi_text:
                        multi_data["text"] = multi_text
                    if multi_audio:
                        temp_audio = save_uploaded_file(multi_audio)
                        if temp_audio:
                            try:
                                upload_audio = jamai.file.upload_file(temp_audio) if jamai else None
                                uri_audio = _get_uri_from_upload(upload_audio)
                                if uri_audio:
                                    multi_data["audio"] = uri_audio
                                else:
                                    st.warning("Audio uploaded but no uri returned.")
                            except Exception as e:
                                st.error(f"Audio upload failed: {e}")
                            finally:
                                _cleanup_temp(temp_audio)
                    if multi_photo:
                        temp_photo = save_uploaded_file(multi_photo)
                        if temp_photo:
                            try:
                                upload_photo = jamai.file.upload_file(temp_photo) if jamai else None
                                uri_photo = _get_uri_from_upload(upload_photo)
                                if uri_photo:
                                    multi_data["photo"] = uri_photo
                                else:
                                    st.warning("Photo uploaded but no uri returned.")
                            except Exception as e:
                                st.error(f"Photo upload failed: {e}")
                            finally:
                                _cleanup_temp(temp_photo)

                    if jamai:
                        response = send_table_row(table_id=TABLE_IDS["multi"], data=multi_data, stream=False)
                    else:
                        response = {"row": {"description": "Simulated integrated description (offline)", "summary": "Simulated strategic summary"}}
                    with st.expander("Raw response from JamAI (multi)"):
                        st.write(response)
                    row = _find_row_dict(response)
                    desc = _extract_field_safe(row, "description", default="No description generated")
                    summary = _extract_field_safe(row, "summary", default="No summary generated")
                    st.subheader("📋 Integrated Description")
                    st.write(desc)
                    st.divider()
                    st.subheader("📢 Strategic Summary")
                    st.success(summary)
                except Exception as e:
                    st.error(f"An error occurred during fusion: {e}")

# === AI Assistant (teammate chat feature, merged) ===
with tab_chat:
    st.header("AI Assistant")
    st.info("Type a message below. If JamAI is configured, it will be sent to the configured chat/table; otherwise a simulated reply is shown.")

    # Use chat table id from secrets or fallback to multi table
    CHAT_TABLE_ID = TABLE_IDS.get("chat", TABLE_IDS["multi"])

    # Display simple chat history in session_state
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # Render previous messages
    for msg in st.session_state.chat_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        st.chat_message(role).write(content)

    # Chat input
    prompt = st.chat_input("Apa jadi? Type here...")
    if prompt:
        # show user message
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        st.chat_message("user").write(prompt)

        # Prepare data row to send - adjust column name 'text' to match your table
        data = {"text": prompt}

        with st.spinner("Contacting AI assistant..."):
            try:
                if jamai:
                    # send as a table row to the chat/table (JamAI expects rows list)
                    response = send_table_row(table_id=CHAT_TABLE_ID, data=data, stream=False)
                else:
                    response = {"row": {"description": None, "summary": None, "assistant_reply": f"Simulated reply: {prompt}"}}

                # show raw response for debugging
                with st.expander("Raw response from JamAI (chat)"):
                    st.write(response)

                row = _find_row_dict(response)
                # prefer assistant_reply, then summary, then description
                assistant_text = _extract_field_safe(row, "assistant_reply")
                if not assistant_text:
                    assistant_text = _extract_field_safe(row, "summary")
                if not assistant_text:
                    assistant_text = _extract_field_safe(row, "description")
                if not assistant_text:
                    assistant_text = "No assistant reply returned."

                # append and show assistant message
                st.session_state.chat_history.append({"role": "assistant", "content": assistant_text})
                st.chat_message("assistant").write(assistant_text)
            except Exception as e:
                st.error(f"Chat failed: {e}")
                # fallback simulated reply
                sim = f"Simulated reply due to error: {str(e)}"
                st.session_state.chat_history.append({"role": "assistant", "content": sim})
                st.chat_message("assistant").write(sim)

# -----------------------
# Sidebar: JamAI debug info
# -----------------------
with st.sidebar.expander("JamAI debug info / Table API"):
    if jamai is None:
        st.write("JamAI client not initialized.")
    else:
        table_obj = getattr(jamai, "table", None)
        st.write("jamai.table type:", type(table_obj))
        if table_obj is not None:
            st.write(sorted(dir(table_obj)))
        st.write("Project ID:", PROJECT_ID)
        st.write("Table IDs (resolved):", TABLE_IDS)

