import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import os
import re
from pathlib import Path
from datetime import datetime

# ─── Drag & drop optionnel ────────────────────────────────────────────────────
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

# ─── StormLib via ctypes ─────────────────────────────────────────────────────
import ctypes, ctypes.util

STORM_AVAILABLE = False
MPYQ_AVAILABLE  = False
_storm_dll      = None

def _load_stormlib():
    global STORM_AVAILABLE, _storm_dll
    dll_path = Path(__file__).parent / "StormLib.dll"
    if dll_path.exists():
        try:
            _storm_dll = ctypes.WinDLL(str(dll_path))
            # SFileOpenArchive(szMpqName, dwPriority, dwFlags, phMpq) -> bool
            _storm_dll.SFileOpenArchive.restype  = ctypes.c_bool
            _storm_dll.SFileOpenArchive.argtypes = [
                ctypes.c_wchar_p, ctypes.c_uint32,
                ctypes.c_uint32,  ctypes.c_void_p,
            ]
            # SFileExtractFile(hMpq, szToExtract, szExtracted, dwSearchScope) -> bool
            _storm_dll.SFileExtractFile.restype  = ctypes.c_bool
            _storm_dll.SFileExtractFile.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p,
                ctypes.c_wchar_p, ctypes.c_uint32,
            ]
            # SFileCloseArchive(hMpq) -> bool
            _storm_dll.SFileCloseArchive.restype  = ctypes.c_bool
            _storm_dll.SFileCloseArchive.argtypes = [ctypes.c_void_p]
            STORM_AVAILABLE = True
        except Exception as e:
            print(f"StormLib.dll trouvée mais non chargée : {e}")

_load_stormlib()

try:
    import mpyq
    MPYQ_AVAILABLE = True
except ImportError:
    MPYQ_AVAILABLE = False

# ─── Config ───────────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "mpq_finder_config.json"

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
            wraplength=320, justify="left",
        ).pack()

    def hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

# ─── Logique de recherche ─────────────────────────────────────────────────────

def get_patch_priority(mpq_name):
    """Extrait le numéro du patch pour la priorité. Plus grand = priorité haute."""
    m = re.search(r'(\d+)', Path(mpq_name).stem)
    return int(m.group(1)) if m else -1

def load_listfiles(source_folder):
    """
    Charge tous les listfiles .txt du dossier source et retourne un dict :
    { "chemin/dans/mpq" : ["patch-4.mpq", ...] }
    La liste est triée par priorité décroissante (priorité haute en premier).
    """
    index = {}  # path_lower -> list of (priority, mpq_name)
    folder = Path(source_folder)

    for txt_file in sorted(folder.glob("*.txt")):
        mpq_name = txt_file.stem + ".mpq"
        priority = get_patch_priority(mpq_name)
        try:
            with open(txt_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    path = line.strip().replace("\\", "/")
                    if not path:
                        continue
                    key = path.lower()
                    if key not in index:
                        index[key] = []
                    index[key].append((priority, mpq_name))
        except Exception as e:
            print(f"Erreur lecture {txt_file} : {e}")

    # Trier par priorité décroissante
    for key in index:
        index[key].sort(key=lambda x: x[0], reverse=True)

    return index

def search_resources(resource_list, index):
    """
    Cherche chaque ressource dans l'index.
    Retourne :
      found    : { original_path: (mpq_name, priority) }
      not_found: [ original_path, ... ]
    """
    found     = {}
    not_found = []

    for res in resource_list:
        res = res.strip().replace("\\", "/")
        if not res:
            continue
        key = res.lower()
        if key in index:
            best_priority, best_mpq = index[key][0]
            found[res] = (best_mpq, best_priority)
        else:
            not_found.append(res)

    return found, not_found

def resolve_dependencies(resource_list, index, dep_m2_skin, dep_wmo_groups):
    """
    À partir d'une liste de ressources, retourne une liste étendue
    avec les dépendances automatiquement ajoutées.
    dep_m2_skin   : True → cherche nomXX.skin pour chaque .m2
    dep_wmo_groups: True → cherche nom000.wmo, nom001.wmo... pour chaque .wmo root
    """
    extra = []

    for res in resource_list:
        res = res.strip().replace("\\", "/")
        p   = Path(res)

        if dep_m2_skin and p.suffix.lower() == ".m2":
            # Cherche nom00.skin, nom01.skin... jusqu'à ce qu'on ne trouve plus
            base = (p.parent / p.stem).as_posix()
            for i in range(20):  # max 20 LODs, largement suffisant
                skin = f"{base}{i:02d}.skin"
                if skin.lower() in index:
                    extra.append(skin)
                elif i > 0:
                    break  # on arrête dès qu'on ne trouve plus

        if dep_wmo_groups and p.suffix.lower() == ".wmo":
            # Ne traiter que les root WMO (pas ceux qui finissent déjà par 3 chiffres)
            if not re.search(r'\d{3}\.wmo$', res, re.IGNORECASE):
                base = (p.parent / p.stem).as_posix()
                for i in range(999):
                    group = f"{base}{i:03d}.wmo"
                    if group.lower() in index:
                        extra.append(group)
                    elif i > 0:
                        break

    # Fusionner sans doublons en gardant l'ordre
    all_resources = list(resource_list)
    seen = {r.lower() for r in all_resources}
    for e in extra:
        if e.lower() not in seen:
            all_resources.append(e)
            seen.add(e.lower())

    return all_resources, len(extra)


def parse_rapport(rapport_path):
    """Extrait tous les chemins de ressources manquantes depuis un rapport filtré."""
    resources = []
    # Capture les lignes du rapport qui commencent par "  - "
    p = re.compile(r'^\s+-\s+([\w/.\-]+\.(m2|wmo|blp))', re.IGNORECASE)
    try:
        with open(rapport_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = p.match(line)
                if m:
                    # Exclure les lignes "Wrong version" qui ont une flèche
                    if "→" not in line:
                        resources.append(m.group(1))
                    else:
                        # Pour wrong version, on prend juste le chemin (avant la flèche)
                        resources.append(m.group(1))
    except Exception as e:
        raise RuntimeError(f"Impossible de lire le rapport : {e}")
    return list(dict.fromkeys(resources))  # dédupliquer en gardant l'ordre

# ─── Extraction MPQ ───────────────────────────────────────────────────────────

def extract_resource_stormlib(mpq_path, resource_path, dest_folder):
    """Extrait une ressource via StormLib.dll (ctypes) en reproduisant l'arborescence."""
    SFILE_OPEN_FROM_MPQ = 0x00000000
    hMpq = ctypes.c_void_p(None)

    ok = _storm_dll.SFileOpenArchive(
        str(mpq_path), 0, SFILE_OPEN_FROM_MPQ, ctypes.byref(hMpq)
    )
    if not ok or not hMpq:
        raise RuntimeError(f"Impossible d'ouvrir l'archive : {mpq_path}")

    out_path = Path(dest_folder) / Path(resource_path.replace("\\", "/"))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # StormLib attend des backslashes pour le chemin interne
    internal = resource_path.replace("/", "\\").encode("utf-8")
    ok = _storm_dll.SFileExtractFile(
        hMpq, internal, str(out_path), SFILE_OPEN_FROM_MPQ
    )
    _storm_dll.SFileCloseArchive(hMpq)

    if not ok:
        err = ctypes.GetLastError()
        raise RuntimeError(f"Extraction échouée (code erreur Windows : {err:#010x})")

def extract_resource_mpyq(mpq_path, resource_path, dest_folder):
    """Extrait une ressource via mpyq en reproduisant l'arborescence."""
    archive = mpyq.MPQArchive(str(mpq_path))
    # mpyq utilise des backslashes
    internal = resource_path.replace("/", "\\")
    data = archive.read_file(internal)
    if data is None:
        raise RuntimeError(f"Fichier non trouvé dans l'archive : {resource_path}")
    out_path = Path(dest_folder) / Path(resource_path.replace("\\", "/"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)

def extract_resource(source_folder, mpq_name, resource_path, dest_folder):
    """Tente l'extraction avec stormlib, puis mpyq en fallback."""
    mpq_path = Path(source_folder) / mpq_name
    if not mpq_path.exists():
        raise FileNotFoundError(f"Archive introuvable : {mpq_path}")

    if STORM_AVAILABLE:
        try:
            extract_resource_stormlib(mpq_path, resource_path, dest_folder)
            return "stormlib"
        except Exception as e:
            if not MPYQ_AVAILABLE:
                raise RuntimeError(f"stormlib a échoué et mpyq n'est pas disponible.\nErreur : {e}")

    if MPYQ_AVAILABLE:
        extract_resource_mpyq(mpq_path, resource_path, dest_folder)
        return "mpyq"

    raise RuntimeError("Ni stormlib ni mpyq ne sont disponibles.\nInstallez au moins l'un des deux.")

# ─── Application principale ───────────────────────────────────────────────────

class MPQFinderApp:
    def __init__(self, root):
        self.root   = root
        self.root.title("MPQ Resource Finder")
        self.root.minsize(680, 780)
        self.config = load_config()
        self.index  = {}       # index des listfiles chargé en mémoire
        self.found     = {}    # résultats de la dernière recherche
        self.not_found = []

        self._build_ui()
        self._check_libs()

        # Charger l'index si un dossier source est déjà mémorisé
        src = self.config.get("source_folder", "")
        if src and os.path.isdir(src):
            self._load_index_bg(src)

    # ── Vérification des libs ─────────────────────────────────────────────────

    def _check_libs(self):
        if STORM_AVAILABLE:
            lib = "StormLib.dll ✅"
            if MPYQ_AVAILABLE:
                lib += "  |  mpyq ✅ (fallback disponible)"
        elif MPYQ_AVAILABLE:
            lib = "mpyq ✅  (StormLib.dll non trouvée — placez-la à côté du script)"
        else:
            lib = "⚠ Ni StormLib.dll ni mpyq trouvés — extraction impossible"
        self.lib_var.set(f"Librairie d'extraction : {lib}")

    # ── Construction de l'UI ──────────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        # En-tête
        header = tk.Frame(root, bg="#1e1e2e", pady=12)
        header.pack(fill="x")
        tk.Label(
            header, text="📦  MPQ Resource Finder",
            bg="#1e1e2e", fg="#cdd6f4",
            font=("Segoe UI", 14, "bold"),
        ).pack()
        tk.Label(
            header,
            text="Recherche et extrait les ressources manquantes depuis vos archives MPQ",
            bg="#1e1e2e", fg="#6c7086",
            font=("Segoe UI", 9),
        ).pack()

        # Zone scrollable
        outer  = tk.Frame(root)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        sb     = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.sf = tk.Frame(canvas)
        self.sf.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        sb.pack(side="right", fill="y")
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

        f = self.sf

        # ── Lib status ────────────────────────────────────────────────────
        self.lib_var = tk.StringVar()
        tk.Label(
            f, textvariable=self.lib_var,
            fg="#555", font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w", padx=16, pady=(6, 0))

        # ── Dossier source (listfiles + MPQ) ──────────────────────────────
        self._section_label(f, "📂  Dossier source  (listfiles .txt + archives .mpq)")
        src_frame = tk.Frame(f)
        src_frame.pack(fill="x", padx=16, pady=4)

        if DND_AVAILABLE:
            tk.Label(
                src_frame,
                text="Glissez-déposez le dossier ici ou utilisez le bouton :",
                fg="#888", font=("Segoe UI", 9, "italic"),
            ).pack(anchor="w")

        self.src_var = tk.StringVar(value=self.config.get("source_folder", ""))
        src_row = tk.Frame(src_frame)
        src_row.pack(fill="x", pady=4)
        src_entry = tk.Entry(src_row, textvariable=self.src_var, font=("Segoe UI", 9))
        src_entry.pack(side="left", fill="x", expand=True)
        tk.Button(
            src_row, text="Parcourir…", command=self._browse_source,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))
        self.index_status = tk.StringVar(value="")
        tk.Label(f, textvariable=self.index_status,
                 fg="#1e66f5", font=("Segoe UI", 9, "italic")).pack(anchor="w", padx=16)

        if DND_AVAILABLE:
            for w in (src_frame, src_row, src_entry):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_source)

        # ── Dossier de destination ─────────────────────────────────────────
        self._section_label(f, "📁  Dossier de destination")
        dest_frame = tk.Frame(f)
        dest_frame.pack(fill="x", padx=16, pady=4)

        if DND_AVAILABLE:
            tk.Label(
                dest_frame,
                text="Glissez-déposez le dossier ici ou utilisez le bouton :",
                fg="#888", font=("Segoe UI", 9, "italic"),
            ).pack(anchor="w")

        self.dest_var = tk.StringVar(value=self.config.get("dest_folder", ""))
        dest_row = tk.Frame(dest_frame)
        dest_row.pack(fill="x", pady=4)
        dest_entry = tk.Entry(dest_row, textvariable=self.dest_var, font=("Segoe UI", 9))
        dest_entry.pack(side="left", fill="x", expand=True)
        tk.Button(
            dest_row, text="Parcourir…", command=self._browse_dest,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))

        if DND_AVAILABLE:
            for w in (dest_frame, dest_row, dest_entry):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_dest)

        # ── Import depuis un rapport filtré ───────────────────────────────
        self._section_label(f, "📋  Import depuis un rapport filtré")
        rapport_frame = tk.Frame(f)
        rapport_frame.pack(fill="x", padx=16, pady=4)

        if DND_AVAILABLE:
            tk.Label(
                rapport_frame,
                text="Glissez-déposez un rapport log_filtered ou utilisez le bouton :",
                fg="#888", font=("Segoe UI", 9, "italic"),
            ).pack(anchor="w")

        self.rapport_var = tk.StringVar(value=self.config.get("last_rapport", ""))
        rap_row = tk.Frame(rapport_frame)
        rap_row.pack(fill="x", pady=4)
        rap_entry = tk.Entry(rap_row, textvariable=self.rapport_var, font=("Segoe UI", 9))
        rap_entry.pack(side="left", fill="x", expand=True)
        tk.Button(
            rap_row, text="Parcourir…", command=self._browse_rapport,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            rap_row, text="⬇ Importer",
            command=self._import_rapport,
            font=("Segoe UI", 9), bg="#40a02b", fg="white", relief="flat",
        ).pack(side="left", padx=(6, 0))

        if DND_AVAILABLE:
            for w in (rapport_frame, rap_row, rap_entry):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_rapport)

        # ── Recherche manuelle ────────────────────────────────────────────
        self._section_label(f, "🔍  Recherche manuelle")
        tk.Label(
            f,
            text="Un chemin par ligne  (ex: world/expansion01/doodads/ghostlands/arch/elvenlands_arch_01.m2)",
            fg="#888", font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w", padx=16)

        self.manual_text = tk.Text(
            f, height=5, font=("Consolas", 9),
            relief="solid", borderwidth=1,
        )
        self.manual_text.pack(fill="x", padx=16, pady=4)

        manual_btn_f = tk.Frame(f)
        manual_btn_f.pack(fill="x", padx=16, pady=(0, 4))
        tk.Button(
            manual_btn_f, text="➕ Ajouter à la liste",
            command=self._add_manual,
            font=("Segoe UI", 9), bg="#1e66f5", fg="white", relief="flat",
        ).pack(side="left")
        tk.Button(
            manual_btn_f, text="Vider",
            command=lambda: self.manual_text.delete("1.0", tk.END),
            font=("Segoe UI", 9),
        ).pack(side="left", padx=6)

        # ── Liste des ressources à chercher ───────────────────────────────
        self._section_label(f, "📃  Ressources à rechercher")
        list_ctrl_f = tk.Frame(f)
        list_ctrl_f.pack(fill="x", padx=16, pady=(4, 0))
        self.res_count_var = tk.StringVar(value="0 ressource(s) dans la liste")
        tk.Label(list_ctrl_f, textvariable=self.res_count_var,
                 fg="#555", font=("Segoe UI", 9)).pack(side="left")
        tk.Button(
            list_ctrl_f, text="🗑 Vider la liste",
            command=self._clear_resource_list,
            font=("Segoe UI", 9), fg="red",
        ).pack(side="right")

        list_frame = tk.Frame(f)
        list_frame.pack(fill="x", padx=16, pady=4)
        self.resource_listbox = tk.Listbox(
            list_frame, height=7, font=("Consolas", 9),
            selectmode=tk.EXTENDED, relief="solid", borderwidth=1,
        )
        list_sb = ttk.Scrollbar(list_frame, orient="vertical",
                                command=self.resource_listbox.yview)
        self.resource_listbox.configure(yscrollcommand=list_sb.set)
        self.resource_listbox.pack(side="left", fill="x", expand=True)
        list_sb.pack(side="right", fill="y")

        tk.Button(
            f, text="✕ Supprimer la sélection",
            command=self._remove_selected,
            font=("Segoe UI", 9), fg="red",
        ).pack(anchor="e", padx=16, pady=(0, 4))

        # ── Dépendances automatiques ─────────────────────────────────────────
        self._section_label(f, "🔗  Résolution automatique des dépendances")
        tk.Label(
            f,
            text="Active la recherche et l'extraction des fichiers liés automatiquement.",
            fg="#888", font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w", padx=16)

        dep_frame = tk.Frame(f)
        dep_frame.pack(fill="x", padx=16, pady=6)

        saved_deps = self.config.get("dependencies", {})

        self.dep_skin_var = tk.BooleanVar(value=saved_deps.get("m2_skin", False))
        self.dep_skin_var.trace_add("write", lambda *_: self._save_state())
        cb_skin = tk.Checkbutton(
            dep_frame,
            text="M2 → .skin associé  (ex: nom.m2 + nom00.skin)",
            variable=self.dep_skin_var,
            font=("Segoe UI", 10),
        )
        cb_skin.pack(anchor="w")
        Tooltip(cb_skin,
            "En vanilla/TBC, les données de géométrie (LOD) sont dans des fichiers "
            ".skin séparés du .m2.\n"
            "Activez cette option si vos archives sont vanilla ou TBC.\n"
            "En WotLK+, les .skin existent aussi mais sont souvent déjà présents."
        )

        self.dep_wmo_var = tk.BooleanVar(value=saved_deps.get("wmo_groups", False))
        self.dep_wmo_var.trace_add("write", lambda *_: self._save_state())
        cb_wmo = tk.Checkbutton(
            dep_frame,
            text="WMO → groupes associés  (ex: nom.wmo + nom000.wmo, nom001.wmo…)",
            variable=self.dep_wmo_var,
            font=("Segoe UI", 10),
        )
        cb_wmo.pack(anchor="w")
        Tooltip(cb_wmo,
            "Un fichier WMO root référence des fichiers de groupe numérotés "
            "(nom000.wmo, nom001.wmo…).\n"
            "Sans eux, le bâtiment sera invisible ou incomplet dans Noggit.\n"
            "Activez cette option pour toutes les versions."
        )

        self.dep_status_var = tk.StringVar(value="")
        tk.Label(
            dep_frame, textvariable=self.dep_status_var,
            fg="#1e66f5", font=("Segoe UI", 9, "italic"),
        ).pack(anchor="w", pady=(4, 0))

        # ── Bouton chercher / extraire ─────────────────────────────────────────
        action_f = tk.Frame(f, pady=4)
        action_f.pack(fill="x", padx=16)
        tk.Button(
            action_f, text="🔍  Rechercher dans les listfiles",
            command=self._search,
            font=("Segoe UI", 10, "bold"),
            bg="#df8e1d", fg="white", relief="flat", pady=8, cursor="hand2",
        ).pack(fill="x", pady=(0, 4))
        tk.Button(
            action_f, text="⚡  Extraire les ressources trouvées",
            command=self._extract,
            font=("Segoe UI", 10, "bold"),
            bg="#1e66f5", fg="white", relief="flat", pady=8, cursor="hand2",
        ).pack(fill="x")

        # ── Résultats ─────────────────────────────────────────────────────
        self._section_label(f, "📊  Résultats")
        res_frame = tk.Frame(f)
        res_frame.pack(fill="x", padx=16, pady=4)

        self.result_text = tk.Text(
            res_frame, height=12, font=("Consolas", 9),
            relief="solid", borderwidth=1, state="disabled",
            bg="#f8f8f8",
        )
        res_vsb = ttk.Scrollbar(res_frame, orient="vertical",
                                command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=res_vsb.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        res_vsb.pack(side="right", fill="y")

        # Tags de couleur
        self.result_text.tag_configure("found",     foreground="#40a02b")
        self.result_text.tag_configure("not_found", foreground="#d20f39")
        self.result_text.tag_configure("info",      foreground="#1e66f5")
        self.result_text.tag_configure("header",    font=("Consolas", 9, "bold"))
        self.result_text.tag_configure("extracted", foreground="#40a02b", font=("Consolas", 9, "bold"))
        self.result_text.tag_configure("error",     foreground="#d20f39")

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

    # ── Dossier source ────────────────────────────────────────────────────────

    def _browse_source(self):
        path = filedialog.askdirectory(title="Dossier contenant les listfiles et MPQ")
        if path:
            self.src_var.set(path)
            self._save_state()
            self._load_index_bg(path)

    def _on_drop_source(self, event):
        path = event.data.strip().strip("{}")
        if os.path.isfile(path):
            path = os.path.dirname(path)
        self.src_var.set(path)
        self._save_state()
        self._load_index_bg(path)

    def _load_index_bg(self, folder):
        self.index_status.set("⏳ Chargement des listfiles…")
        self.root.update()
        try:
            self.index = load_listfiles(folder)
            count = len(self.index)
            txt_count = len(list(Path(folder).glob("*.txt")))
            self.index_status.set(
                f"✅ {txt_count} listfile(s) chargé(s) — {count:,} ressources indexées"
            )
        except Exception as e:
            self.index_status.set(f"❌ Erreur : {e}")

    # ── Dossier destination ───────────────────────────────────────────────────

    def _browse_dest(self):
        path = filedialog.askdirectory(title="Dossier de destination")
        if path:
            self.dest_var.set(path)
            self._save_state()

    def _on_drop_dest(self, event):
        path = event.data.strip().strip("{}")
        if os.path.isfile(path):
            path = os.path.dirname(path)
        self.dest_var.set(path)
        self._save_state()

    # ── Rapport filtré ────────────────────────────────────────────────────────

    def _browse_rapport(self):
        path = filedialog.askopenfilename(
            title="Sélectionner un rapport filtré",
            filetypes=[("Fichiers texte", "*.txt"), ("Tous les fichiers", "*.*")],
        )
        if path:
            self.rapport_var.set(path)
            self._save_state()

    def _on_drop_rapport(self, event):
        path = event.data.strip().strip("{}")
        self.rapport_var.set(path)
        self._save_state()

    def _import_rapport(self):
        path = self.rapport_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Fichier introuvable",
                                 "Veuillez sélectionner un rapport valide.")
            return
        try:
            resources = parse_rapport(path)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
            return

        if not resources:
            messagebox.showinfo("Aucune ressource",
                                "Aucune ressource trouvée dans ce rapport.")
            return

        added = 0
        existing = list(self.resource_listbox.get(0, tk.END))
        for res in resources:
            if res not in existing:
                self.resource_listbox.insert(tk.END, res)
                added += 1

        self._update_count()
        self.status_var.set(
            f"✅ {added} ressource(s) importée(s) depuis le rapport "
            f"({len(resources) - added} doublon(s) ignoré(s))"
        )

    # ── Recherche manuelle ────────────────────────────────────────────────────

    def _add_manual(self):
        raw = self.manual_text.get("1.0", tk.END).strip()
        if not raw:
            return
        lines    = [l.strip().replace("\\", "/") for l in raw.splitlines() if l.strip()]
        existing = list(self.resource_listbox.get(0, tk.END))
        added    = 0
        for line in lines:
            if line not in existing:
                self.resource_listbox.insert(tk.END, line)
                added += 1
        self.manual_text.delete("1.0", tk.END)
        self._update_count()
        self.status_var.set(f"✅ {added} ressource(s) ajoutée(s).")

    def _remove_selected(self):
        for idx in reversed(self.resource_listbox.curselection()):
            self.resource_listbox.delete(idx)
        self._update_count()

    def _clear_resource_list(self):
        self.resource_listbox.delete(0, tk.END)
        self.found     = {}
        self.not_found = []
        self._update_count()
        self._clear_results()

    def _update_count(self):
        n = self.resource_listbox.size()
        self.res_count_var.set(f"{n} ressource(s) dans la liste")

    # ── Recherche ─────────────────────────────────────────────────────────────

    def _search(self):
        if not self.index:
            messagebox.showwarning("Index vide",
                                   "Aucun listfile chargé. Vérifiez le dossier source.")
            return

        resources = list(self.resource_listbox.get(0, tk.END))
        if not resources:
            messagebox.showinfo("Liste vide",
                                "Ajoutez des ressources à rechercher.")
            return

        # Résolution des dépendances
        dep_m2   = self.dep_skin_var.get()
        dep_wmo  = self.dep_wmo_var.get()
        if dep_m2 or dep_wmo:
            resources, n_extra = resolve_dependencies(
                resources, self.index, dep_m2, dep_wmo
            )
            self.dep_status_var.set(f"🔗 {n_extra} dépendance(s) ajoutée(s) à la recherche")
            # Mettre à jour la listbox
            self.resource_listbox.delete(0, tk.END)
            for r in resources:
                self.resource_listbox.insert(tk.END, r)
            self._update_count()
        else:
            self.dep_status_var.set("")

        self.status_var.set("⏳ Recherche en cours…")
        self.root.update()

        self.found, self.not_found = search_resources(resources, self.index)

        self._clear_results()
        self._write_result(
            f"══ RÉSULTATS DE RECHERCHE — {datetime.now().strftime('%H:%M:%S')} ══\n",
            "header"
        )

        if self.found:
            self._write_result(
                f"\n✅ TROUVÉES ({len(self.found)}) :\n", "found"
            )
            for path, (mpq, _) in self.found.items():
                self._write_result(f"  {path}\n", "found")
                self._write_result(f"    → {mpq}\n", "info")

        if self.not_found:
            self._write_result(
                f"\n❌ INTROUVABLES ({len(self.not_found)}) :\n", "not_found"
            )
            for path in self.not_found:
                self._write_result(f"  {path}\n", "not_found")

        total = len(resources)
        self._write_result(
            f"\n{'─'*48}\n"
            f"Total : {len(self.found)}/{total} trouvée(s)  |  "
            f"{len(self.not_found)} introuvable(s)\n",
            "header"
        )

        self.status_var.set(
            f"✅ Recherche terminée — {len(self.found)} trouvée(s), "
            f"{len(self.not_found)} introuvable(s)"
        )

    # ── Extraction ────────────────────────────────────────────────────────────

    def _extract(self):
        if not self.found:
            messagebox.showinfo("Aucun résultat",
                                "Lancez d'abord une recherche.")
            return

        dest = self.dest_var.get().strip()
        if not dest or not os.path.isdir(dest):
            messagebox.showerror("Dossier invalide",
                                 "Veuillez sélectionner un dossier de destination valide.")
            return

        src = self.src_var.get().strip()
        if not src:
            messagebox.showerror("Dossier source manquant",
                                 "Le dossier source (MPQ) n'est pas défini.")
            return

        if not STORM_AVAILABLE and not MPYQ_AVAILABLE:
            messagebox.showerror(
                "Librairie manquante",
                "Ni stormlib ni mpyq ne sont installés.\n"
                "Installez stormlib : pip install stormlib"
            )
            return

        total   = len(self.found)
        ok      = []
        errors  = []

        self._clear_results()
        self._write_result(
            f"══ EXTRACTION — {datetime.now().strftime('%H:%M:%S')} ══\n", "header"
        )

        for i, (res_path, (mpq_name, _)) in enumerate(self.found.items(), 1):
            self.status_var.set(f"⏳ Extraction {i}/{total} : {Path(res_path).name}")
            self.root.update()
            try:
                lib_used = extract_resource(src, mpq_name, res_path, dest)
                ok.append(res_path)
                self._write_result(f"  ✅ {res_path}\n", "extracted")
                self._write_result(f"     ← {mpq_name}  [{lib_used}]\n", "info")
            except Exception as e:
                errors.append((res_path, str(e)))
                self._write_result(f"  ❌ {res_path}\n", "error")
                self._write_result(f"     ← {mpq_name}\n", "info")
                self._write_result(f"     Erreur : {e}\n", "error")

        self._write_result(
            f"\n{'─'*48}\n"
            f"Extraites : {len(ok)}/{total}  |  Erreurs : {len(errors)}\n"
            f"Destination : {dest}\n",
            "header"
        )

        self.status_var.set(
            f"✅ Extraction terminée — {len(ok)}/{total} fichier(s) extrait(s)"
        )

    # ── Résultats texte ───────────────────────────────────────────────────────

    def _clear_results(self):
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.configure(state="disabled")

    def _write_result(self, text, tag=None):
        self.result_text.configure(state="normal")
        if tag:
            self.result_text.insert(tk.END, text, tag)
        else:
            self.result_text.insert(tk.END, text)
        self.result_text.configure(state="disabled")
        self.result_text.see(tk.END)

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    def _save_state(self):
        self.config["source_folder"] = self.src_var.get()
        self.config["dest_folder"]   = self.dest_var.get()
        self.config["last_rapport"]  = self.rapport_var.get()
        self.config["dependencies"] = {
            "m2_skin":    self.dep_skin_var.get(),
            "wmo_groups": self.dep_wmo_var.get(),
        }
        save_config(self.config)

# ─── Point d'entrée ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    app = MPQFinderApp(root)
    root.mainloop()
