"""Local-only control surface; the supervisor runs in a separate process."""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from mpt_factory.common import inside, now, read_json
from mpt_factory.config import load_config
from mpt_factory.db import Database
from mpt_factory.jobs import create_job
from mpt_factory.services import Services


def main():
    st.set_page_config(page_title="MPT Agent Factory", layout="wide")
    st.title("MPT Agent Factory · v0.1")
    st.caption("Supervisor determinista · una generación activa · revisión visual pendiente")
    try:
        cfg = load_config()
        db = Database(cfg.db)
    except Exception as exc:
        st.error(f"Configuración: {exc}")
        return
    with st.expander("Crear un job"):
        default = {"params": {"video_subject": "Una escena de montaña al amanecer",
            "video_source": "openai_image", "video_language": "es-ES",
            "voice_name": "es-ES-AlvaroNeural-Male", "video_aspect": "9:16", "bgm_type": ""},
            "profile": "balanced", "reference_mode": "user_first", "references": []}
        with st.form("create"):
            payload = st.text_area("Job JSON (rutas absolutas para archivos locales)", json.dumps(default, ensure_ascii=False, indent=2), height=260)
            queue = st.checkbox("Poner en cola", value=True)
            submitted = st.form_submit_button("Crear job")
        if submitted:
            try:
                job = create_job(cfg, db, json.loads(payload), cfg.file.parent)
                if queue:
                    db.transition(job, "queued")
                st.success(f"Job creado: {job}")
            except Exception as exc:
                st.error(str(exc))

    @st.fragment(run_every=3)
    def monitor():
        supervisor = read_json(cfg.data / "supervisor.json", {})
        connected = supervisor.get("status") == "running" and now() - supervisor.get("time", 0) < 30
        st.info("Supervisor activo" if connected else "Supervisor sin heartbeat reciente. Inícialo en otra terminal.")
        rows = db.jobs()
        columns = st.columns(4)
        for column, states, label in zip(columns,
            [{"queued"}, {"preparing", "running", "collecting", "evaluating"}, {"succeeded"}, {"failed", "interrupted"}],
            ["En cola", "Activos", "Completados", "Fallidos / interrumpidos"]):
            column.metric(label, sum(r["state"] in states for r in rows))
        if not rows:
            st.write("No hay jobs todavía.")
            return
        st.dataframe([{k: r[k] for k in ("id", "title", "state", "progress", "error")} for r in rows], hide_index=True, width="stretch")
        selected = st.selectbox("Job", [r["id"] for r in rows], format_func=lambda key: next(f"{r['title'][:70]} · {r['state']} · {key[:8]}" for r in rows if r["id"] == key))
        job = db.get(selected)
        st.progress(min(100, max(0, job["progress"] or 0)) / 100)
        a, b = st.columns(2)
        if a.button("Encolar", disabled=job["state"] != "created"):
            db.transition(selected, "queued")
            st.rerun(scope="fragment")
        if b.button("Solicitar cancelación", disabled=job["state"] in {"succeeded", "failed", "cancelled", "interrupted"}):
            db.cancel(selected)
            st.rerun(scope="fragment")
        if job["result"]:
            st.json(json.loads(job["result"]))
        items = db.artifacts(selected)
        st.write(f"Artifacts registrados: {len(items)}")
        for item in items:
            try:
                file = inside(Path(item["path"]), cfg.data)
                if item["kind"] == "final_video" and file.is_file():
                    st.video(str(file))
            except ValueError:
                st.error("Artifact fuera del directorio de Factory")
        with st.expander("Imágenes y diagnostics"):
            images = [a for a in items if a["kind"] == "image"][:20]
            for image in images:
                file = inside(Path(image["path"]), cfg.data)
                if file.exists():
                    st.image(str(file), caption=file.name, width=220)
            for item in items:
                if item["kind"] == "diagnostics":
                    st.json(read_json(inside(Path(item["path"]), cfg.data)))
            st.dataframe(items, hide_index=True)
        with st.expander("Eventos y parámetros"):
            st.json(json.loads(job["spec"]))
            st.dataframe(db.events(selected)[-100:], hide_index=True)
    monitor()
    with st.expander("Servicios · 8080 / 8090 / 8188 / 8501"):
        if st.button("Comprobar servicios"):
            st.dataframe(Services(cfg, db).all_health(), hide_index=True)
    st.caption("Sin auto-merge ni publicación. Los experimentos se crean mediante la CLI en worktrees aislados.")


if __name__ == "__main__":
    main()
