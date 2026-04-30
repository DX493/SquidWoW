import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import re
import os
from datetime import datetime
from pathlib import Path

# ─── Drag & drop optionnel ────────────────────────────────────────────────────
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

# ─── Constantes ───────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "filters.json"

BUILTIN_SOURCES = {
    "AsyncObject.h": {
        "description": "Fichiers WMO et M2 introuvables lors du chargement.",
        "default": True,
        "managed_by_smart": True,
    },
    "Model.cpp": {
        "description": "Erreurs de version M2 (Wrong M2 version).",
        "default": True,
        "managed_by_smart": True,
    },
    "TextureManager.cpp": {
        "description": "Textures introuvables (.blp).",
        "default": True,
        "managed_by_smart": True,
    },
    "context.inl": {
        "description": "Erreurs OpenGL (GL_INVALID_OPERATION).",
        "default": False,
        "managed_by_smart": False,
    },
    "AsyncLoader.cpp": {
        "description": "Erreurs de chargement asynchrone inconnues.",
        "default": False,
        "managed_by_smart": False,
    },
}

SMART_CATEGORIES = {
    "wrong_version": {
        "label": "Wrong M2 version",
        "description": "Fichiers M2 avec une version incompatible (Wrong M2 version XXX).",
    },
    "wmo_not_found": {
        "label": "WMO introuvables",
        "description": "Fichiers WMO impossibles à charger (.wmo could not be loaded).",
    },
    "m2_not_found": {
        "label": "M2 introuvables",
        "description": "Fichiers M2 impossibles à charger (.m2 could not be loaded).\nLes fichiers déjà capturés par 'Wrong M2 version' ne sont pas dupliqués ici.",
    },
    "texture_not_found": {
        "label": "Textures introuvables",
        "description": "Textures .blp introuvables (file not found: '...').",
    },
}

# ─── Gestion de la configuration JSON ─────────────────────────────────────────

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur sauvegarde config : {e}")

# ─── Parsing du log ───────────────────────────────────────────────────────────

def parse_log(filepath, smart_enabled, builtin_enabled, custom_filters):
    results = {
        "wrong_version":   [],   # list of (filepath, version)
        "wmo_not_found":   [],   # list of filepath strings
        "m2_not_found":    [],   # list of filepath strings
        "texture_not_found": [], # list of filepath strings
        "builtin_extra":   {},   # source -> list of raw lines
        "custom":          {},   # filter_name -> list of raw lines
    }

    wrong_version_files = set()  # pour dédupliquer M2 introuvables

    # Patterns
    p_wrong_version = re.compile(
        r'Error loading file "([^"]+\.m2)".*?Wrong M2 version (\d+)', re.IGNORECASE
    )
    p_wmo     = re.compile(r'File ([\w/.\-]+\.wmo) could not be loaded', re.IGNORECASE)
    p_m2      = re.compile(r'File ([\w/.\-]+\.m2) could not be loaded',  re.IGNORECASE)
    p_texture = re.compile(r"file not found: '([^']+\.blp)'",            re.IGNORECASE)
    p_source  = re.compile(r'\(([^:)]+\.[a-zA-Z]+):\d+\)',              re.IGNORECASE)
    p_error   = re.compile(r'\[Error\]')

    # Sources brutes supplémentaires activées (non gérées par les catégories intelligentes)
    extra_sources = {
        src: []
        for src, info in BUILTIN_SOURCES.items()
        if not info["managed_by_smart"] and builtin_enabled.get(src, False)
    }

    # Filtres custom actifs
    custom_patterns = {}
    for cf in custom_filters:
        if cf.get("enabled", True):
            try:
                custom_patterns[cf["name"]] = re.compile(
                    re.escape(cf["pattern"]), re.IGNORECASE
                )
                results["custom"][cf["name"]] = []
            except re.error:
                pass

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        raise RuntimeError(f"Impossible de lire le fichier : {e}")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        matched_smart = False

        # ── Catégories intelligentes ──────────────────────────────────────
        if smart_enabled.get("wrong_version", False):
            m = p_wrong_version.search(line)
            if m:
                fp, ver = m.group(1), m.group(2)
                results["wrong_version"].append((fp, ver))
                wrong_version_files.add(fp.lower())
                matched_smart = True

        if not matched_smart and smart_enabled.get("wmo_not_found", False):
            m = p_wmo.search(line)
            if m:
                results["wmo_not_found"].append(m.group(1))
                matched_smart = True

        if not matched_smart and smart_enabled.get("m2_not_found", False):
            m = p_m2.search(line)
            if m:
                fp = m.group(1)
                if fp.lower() not in wrong_version_files:
                    results["m2_not_found"].append(fp)
                matched_smart = True

        if not matched_smart and smart_enabled.get("texture_not_found", False):
            m = p_texture.search(line)
            if m:
                results["texture_not_found"].append(m.group(1))
                matched_smart = True

        # ── Sources brutes supplémentaires ────────────────────────────────
        if not matched_smart and extra_sources and p_error.search(line):
            src_m = p_source.search(line)
            if src_m:
                src = src_m.group(1)
                if src in extra_sources:
                    extra_sources[src].append(line)

        # ── Filtres custom ────────────────────────────────────────────────
        for name, pattern in custom_patterns.items():
            if pattern.search(line):
                results["custom"][name].append(line)

    results["builtin_extra"] = extra_sources
    return results

# ─── Génération du rapport ────────────────────────────────────────────────────

def generate_report(results, log_path):
    now   = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    lines = []
    total = 0

    SEP  = "=" * 64
    SEP2 = "-" * 64

    lines += [
        SEP,
        f"  RAPPORT DE LOG  —  {now}",
        f"  Source : {log_path}",
        SEP,
    ]

    def section(title, items, formatter=None):
        nonlocal total
        count = len(items)
        total += count
        s = "s" if count != 1 else ""
        lines.append(f"\n[{title}]  ({count} erreur{s})")
        lines.append(SEP2)
        if count == 0:
            lines.append("  Aucune erreur trouvée.")
        else:
            for item in items:
                txt = formatter(item) if formatter else f"- {item}"
                lines.append(f"  {txt}")

    # Wrong version
    if results["wrong_version"]:
        section(
            "WRONG M2 VERSION",
            results["wrong_version"],
            lambda x: f"- {x[0]}  →  version {x[1]}",
        )

    # WMO
    if results["wmo_not_found"]:
        section("WMO INTROUVABLES", results["wmo_not_found"])

    # M2
    if results["m2_not_found"]:
        section("M2 INTROUVABLES", results["m2_not_found"])

    # Textures (dédupliquées)
    if results["texture_not_found"]:
        seen, deduped = set(), []
        for t in results["texture_not_found"]:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        section("TEXTURES INTROUVABLES", deduped)

    # Sources brutes
    for src, src_lines in results["builtin_extra"].items():
        if src_lines:
            section(f"SOURCE : {src}", src_lines)

    # Custom
    for name, custom_lines in results["custom"].items():
        if custom_lines:
            section(f"FILTRE CUSTOM : {name}", custom_lines)

    s = "s" if total != 1 else ""
    lines += [
        f"\n{SEP}",
        f"  TOTAL : {total} erreur{s} trouvée{s}",
        SEP,
    ]
    return "\n".join(lines)

# ─── Tooltip ──────────────────────────────────────────────────────────────────

class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text   = text
        self.tip    = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip, text=self.text,
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Segoe UI", 9), padx=8, pady=5,
            wraplength=300, justify="left",
        ).pack()

    def hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

# ─── Application principale ───────────────────────────────────────────────────

class LogFilterApp:
    def __init__(self, root):
        self.root           = root
        self.root.title("Noggit Log Filter")
        self.root.minsize(620, 720)
        self.config         = load_config()
        self.smart_vars     = {}
        self.builtin_vars   = {}
        self.custom_filters = self.config.get("custom_filters", [])
        self.extra_detected = []  # sources auto-détectées dans le log

        self._build_ui()
        # Recharger les sources détectées si un chemin est déjà mémorisé
        path = self.config.get("last_log_path", "")
        if path and os.path.exists(path):
            self._detect_extra_sources(path)

    # ── Construction de l'UI ──────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # En-tête
        header = tk.Frame(root, bg="#1e1e2e", pady=12)
        header.pack(fill="x")
        tk.Label(
            header, text="🔍  Noggit Log Filter",
            bg="#1e1e2e", fg="#cdd6f4",
            font=("Segoe UI", 14, "bold"),
        ).pack()
        tk.Label(
            header, text="Analyse et filtre les erreurs de votre fichier log Noggit",
            bg="#1e1e2e", fg="#6c7086",
            font=("Segoe UI", 9),
        ).pack()

        # Zone scrollable
        outer = tk.Frame(root)
        outer.pack(fill="both", expand=True)

        canvas    = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.sf   = tk.Frame(canvas)

        self.sf.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.sf, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

        f = self.sf

        # ── Fichier log ───────────────────────────────────────────────────
        self._section_label(f, "📂  Fichier log")
        file_frame = tk.Frame(f)
        file_frame.pack(fill="x", padx=16, pady=4)

        self.file_var = tk.StringVar(value=self.config.get("last_log_path", ""))

        if DND_AVAILABLE:
            lbl = tk.Label(
                file_frame,
                text="Glissez-déposez votre fichier ici, ou utilisez le bouton :",
                fg="#888", font=("Segoe UI", 9, "italic"),
            )
            lbl.pack(anchor="w")
            lbl.drop_target_register(DND_FILES)
            lbl.dnd_bind("<<Drop>>", self._on_drop)

        row = tk.Frame(file_frame)
        row.pack(fill="x", pady=4)
        tk.Entry(row, textvariable=self.file_var, font=("Segoe UI", 9)).pack(
            side="left", fill="x", expand=True
        )
        tk.Button(
            row, text="Parcourir…", command=self._browse_file,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))

        # ── Catégories intelligentes ──────────────────────────────────────
        self._section_label(f, "⚙️  Catégories intelligentes")
        tk.Label(
            f,
            text="Erreurs reconnues et formatées automatiquement.",
            fg="#888", font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w", padx=16)

        smart_f = tk.Frame(f)
        smart_f.pack(fill="x", padx=16, pady=6)
        saved_smart = self.config.get("smart_categories", {})

        for key, info in SMART_CATEGORIES.items():
            var = tk.BooleanVar(value=saved_smart.get(key, True))
            var.trace_add("write", lambda *_: self._save_state())
            self.smart_vars[key] = var
            cb = tk.Checkbutton(
                smart_f, text=info["label"], variable=var,
                font=("Segoe UI", 10),
            )
            cb.pack(anchor="w")
            Tooltip(cb, info["description"])

        # ── Sources connues ───────────────────────────────────────────────
        self._section_label(f, "🔧  Sources connues")
        tk.Label(
            f,
            text="Survolez chaque case pour comprendre les erreurs correspondantes.",
            fg="#888", font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w", padx=16)

        self.builtin_frame = tk.Frame(f)
        self.builtin_frame.pack(fill="x", padx=16, pady=6)
        self._rebuild_builtin_sources()

        # ── Filtres personnalisés ─────────────────────────────────────────
        self._section_label(f, "✏️  Filtres personnalisés")
        tk.Label(
            f,
            text="Ajoutez vos propres mots-clés ou patterns (texte brut).",
            fg="#888", font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w", padx=16)

        self.custom_list_frame = tk.Frame(f)
        self.custom_list_frame.pack(fill="x", padx=16, pady=2)
        self._rebuild_custom_list()

        add_f = tk.Frame(f)
        add_f.pack(fill="x", padx=16, pady=6)

        tk.Label(add_f, text="Nom :",        font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        self.new_name    = tk.Entry(add_f, width=16, font=("Segoe UI", 9))
        self.new_name.grid(row=0, column=1, padx=4)

        tk.Label(add_f, text="Pattern :",    font=("Segoe UI", 9)).grid(row=0, column=2, sticky="w")
        self.new_pattern = tk.Entry(add_f, width=22, font=("Segoe UI", 9))
        self.new_pattern.grid(row=0, column=3, padx=4)

        tk.Label(add_f, text="Description :", font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=4)
        self.new_desc    = tk.Entry(add_f, width=40, font=("Segoe UI", 9))
        self.new_desc.grid(row=1, column=1, columnspan=3, padx=4, sticky="ew")

        tk.Button(
            add_f, text="➕ Ajouter", command=self._add_custom_filter,
            font=("Segoe UI", 9), bg="#40a02b", fg="white", relief="flat",
        ).grid(row=0, column=4, rowspan=2, padx=10, sticky="ns")

        # ── Bouton générer ────────────────────────────────────────────────
        btn_f = tk.Frame(root, pady=8)
        btn_f.pack(fill="x", padx=10)
        tk.Button(
            btn_f, text="⚡  Générer le rapport",
            command=self._generate,
            font=("Segoe UI", 11, "bold"),
            bg="#1e66f5", fg="white",
            pady=10, relief="flat", cursor="hand2",
        ).pack(fill="x")

        # Barre de statut
        self.status_var = tk.StringVar(value="Prêt.")
        tk.Label(
            root, textvariable=self.status_var,
            fg="#555", font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 6))

    def _section_label(self, parent, title):
        f = tk.Frame(parent, bg="#dce0e8", pady=1)
        f.pack(fill="x", pady=(14, 0))
        tk.Label(
            f, text=title, bg="#dce0e8",
            font=("Segoe UI", 10, "bold"), padx=10, pady=5,
        ).pack(anchor="w")

    # ── Sources connues ───────────────────────────────────────────────────────

    def _rebuild_builtin_sources(self):
        for w in self.builtin_frame.winfo_children():
            w.destroy()

        saved = self.config.get("builtin_sources", {})

        for src, info in BUILTIN_SOURCES.items():
            if src not in self.builtin_vars:
                var = tk.BooleanVar(value=saved.get(src, info["default"]))
                var.trace_add("write", lambda *_: self._save_state())
                self.builtin_vars[src] = var

            label = src
            note  = ""
            color = "#000"
            tip   = info["description"]

            if info["managed_by_smart"]:
                note  = "  ⚠ déjà géré par les catégories intelligentes"
                color = "#999"
                tip  += "\n\n⚠ Cette source est couverte par les catégories intelligentes ci-dessus."

            cb = tk.Checkbutton(
                self.builtin_frame,
                text=label + note,
                variable=self.builtin_vars[src],
                font=("Segoe UI", 9), fg=color,
            )
            cb.pack(anchor="w")
            Tooltip(cb, tip)

        # Sources auto-détectées inconnues
        for src in self.extra_detected:
            if src in BUILTIN_SOURCES:
                continue
            if src not in self.builtin_vars:
                var = tk.BooleanVar(value=False)
                var.trace_add("write", lambda *_: self._save_state())
                self.builtin_vars[src] = var

            cb = tk.Checkbutton(
                self.builtin_frame,
                text=f"{src}  🔍 détecté automatiquement",
                variable=self.builtin_vars[src],
                font=("Segoe UI", 9), fg="#1e66f5",
            )
            cb.pack(anchor="w")
            Tooltip(cb, "Source inconnue détectée automatiquement dans le log.\nElle sera filtrée par lignes brutes [Error].")

    # ── Filtres custom ────────────────────────────────────────────────────────

    def _rebuild_custom_list(self):
        for w in self.custom_list_frame.winfo_children():
            w.destroy()

        if not self.custom_filters:
            tk.Label(
                self.custom_list_frame,
                text="Aucun filtre personnalisé pour le moment.",
                fg="#aaa", font=("Segoe UI", 9, "italic"),
            ).pack(anchor="w")
            return

        for i, cf in enumerate(self.custom_filters):
            row = tk.Frame(self.custom_list_frame)
            row.pack(fill="x", pady=1)

            key = f"custom_{i}"
            if key not in self.builtin_vars:
                v = tk.BooleanVar(value=cf.get("enabled", True))
                self.builtin_vars[key] = v

            def _toggle(idx=i):
                self.custom_filters[idx]["enabled"] = self.builtin_vars[f"custom_{idx}"].get()
                self._save_state()

            self.builtin_vars[key].trace_add("write", lambda *_, idx=i: _toggle(idx))

            cb = tk.Checkbutton(
                row,
                text=f"{cf['name']}  [{cf['pattern']}]",
                variable=self.builtin_vars[key],
                font=("Segoe UI", 9),
            )
            cb.pack(side="left")
            if cf.get("description"):
                Tooltip(cb, cf["description"])

            tk.Button(
                row, text="✕", fg="red", relief="flat",
                cursor="hand2", font=("Segoe UI", 8),
                command=lambda idx=i: self._remove_custom_filter(idx),
            ).pack(side="left", padx=4)

    def _add_custom_filter(self):
        name    = self.new_name.get().strip()
        pattern = self.new_pattern.get().strip()
        desc    = self.new_desc.get().strip()

        if not name or not pattern:
            messagebox.showwarning("Champs requis", "Le nom et le pattern sont obligatoires.")
            return

        self.custom_filters.append(
            {"name": name, "pattern": pattern, "description": desc, "enabled": True}
        )
        self.new_name.delete(0, tk.END)
        self.new_pattern.delete(0, tk.END)
        self.new_desc.delete(0, tk.END)

        self._rebuild_custom_list()
        self._save_state()

    def _remove_custom_filter(self, idx):
        self.custom_filters.pop(idx)
        # Nettoyer les vars obsolètes
        keys_to_del = [k for k in self.builtin_vars if k.startswith("custom_")]
        for k in keys_to_del:
            del self.builtin_vars[k]
        self._rebuild_custom_list()
        self._save_state()

    # ── Fichier ───────────────────────────────────────────────────────────────

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Sélectionner le fichier log",
            filetypes=[("Fichiers log", "*.txt *.log"), ("Tous les fichiers", "*.*")],
        )
        if path:
            self.file_var.set(path)
            self._save_state()
            self._detect_extra_sources(path)

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")
        self.file_var.set(path)
        self._save_state()
        self._detect_extra_sources(path)

    def _detect_extra_sources(self, filepath):
        """Détecte automatiquement les sources [Error] inconnues dans le log."""
        p = re.compile(r'\(([^:)]+\.[a-zA-Z]+):\d+\).*?\[Error\]', re.IGNORECASE)
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = p.search(line)
                    if m:
                        src = m.group(1)
                        if src not in BUILTIN_SOURCES and src not in self.extra_detected:
                            self.extra_detected.append(src)
        except Exception:
            pass
        self._rebuild_builtin_sources()

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    def _save_state(self):
        self.config["last_log_path"]    = self.file_var.get()
        self.config["smart_categories"] = {k: v.get() for k, v in self.smart_vars.items()}
        self.config["builtin_sources"]  = {
            k: v.get() for k, v in self.builtin_vars.items()
            if not k.startswith("custom_")
        }
        self.config["custom_filters"] = self.custom_filters
        save_config(self.config)

    # ── Génération ────────────────────────────────────────────────────────────

    def _generate(self):
        path = self.file_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Fichier introuvable",
                                 "Veuillez sélectionner un fichier log valide.")
            return

        smart_enabled   = {k: v.get() for k, v in self.smart_vars.items()}
        builtin_enabled = {
            k: v.get() for k, v in self.builtin_vars.items()
            if not k.startswith("custom_")
        }

        try:
            self.status_var.set("⏳ Analyse en cours…")
            self.root.update()
            results = parse_log(path, smart_enabled, builtin_enabled, self.custom_filters)
            report  = generate_report(results, path)
        except Exception as e:
            messagebox.showerror("Erreur d'analyse", str(e))
            self.status_var.set("❌ Erreur lors de l'analyse.")
            return

        log_dir  = os.path.dirname(path)
        out_name = f"log_filtered_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        out_path = os.path.join(log_dir, out_name)

        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(report)
        except Exception as e:
            messagebox.showerror("Erreur d'écriture", str(e))
            return

        total = (
            len(results["wrong_version"])
            + len(results["wmo_not_found"])
            + len(results["m2_not_found"])
            + len(set(results["texture_not_found"]))
            + sum(len(v) for v in results["builtin_extra"].values())
            + sum(len(v) for v in results["custom"].values())
        )

        self.status_var.set(f"✅ Rapport généré : {out_name}  ({total} erreurs)")
        messagebox.showinfo(
            "Rapport généré",
            f"{total} erreur(s) trouvée(s).\n\nFichier enregistré :\n{out_path}",
        )

# ─── Point d'entrée ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    app = LogFilterApp(root)
    root.mainloop()
