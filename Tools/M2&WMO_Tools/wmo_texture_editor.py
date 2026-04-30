#!/usr/bin/env python3
"""
WMO Texture Editor
Visualise et modifie les chemins de texture des fichiers WMO root
(Vanilla → Retail/Legion+).
Dépendance optionnelle : tkinterdnd2 (pip install tkinterdnd2)
 
Notes format WMO :
  - Les magics de chunks sont stockés INVERSÉS dans le fichier
    (ex : "MVER" → bytes b'REVM', "MOHD" → b'DHOM', etc.)
  - MOTX  : pool de noms de texture (chaînes ASCII null-terminated concaténées)
  - MOMT  : 64 bytes/matériau ; offsets texture à 0x0C, 0x18, 0x24 (→ MOTX ou FDID Legion+)
  - Legion+: MOHD flags bit 0x4 → les valeurs dans MOMT sont des FileDataIDs, pas des offsets MOTX
"""
 
import sys, struct, shutil
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    import tkinter as tk
    HAS_DND = False
if HAS_DND:
    import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
 
# ─── Helpers ─────────────────────────────────────────────────────────────────
 
def ru32(d, o): return struct.unpack_from('<I', d, o)[0]
def wu32(b, o, v): struct.pack_into('<I', b, o, v)
 
def str_at(data, offset: int) -> str:
    """Lit une chaîne null-terminated à l'offset donné dans data."""
    if offset < 0 or offset >= len(data): return ""
    end = offset
    while end < len(data) and data[end] != 0:
        end += 1
    return data[offset:end].decode('ascii', errors='replace')
 
# ─── Constantes format WMO ───────────────────────────────────────────────────
 
# MOHD flags (uint16 à offset 0x3C dans le chunk MOHD)
MOHD_FLAG_FDID = 0x4    # Legion+ : textures par FileDataID
 
# MOMT : 64 bytes par matériau
MOMT_SIZE = 64
MOMT_TEX1 = 0x0C        # diffuseNameIndex  (offset MOTX, ou FDID)
MOMT_TEX2 = 0x18        # specularNameIndex (offset MOTX, ou FDID)
MOMT_TEX3 = 0x24        # texture_3         (offset MOTX, ou FDID)
 
WMO_SHADERS = {
    0:  "Diffuse",
    1:  "Specular",
    2:  "Metal",
    3:  "Env",
    4:  "Opaque",
    5:  "EnvMetal",
    6:  "TwoLayerDiffuse",
    7:  "TwoLayerEnvMetal",
    8:  "TwoLayerTerrain",
    9:  "DiffuseEmissive",
    10: "Waterwindow",
    11: "MaskedEnvMetal",
    12: "EnvMetalEmissive",
    13: "TwoLayerDiffuseOpaque",
    14: "TwoLayerDiffuseEmissive",
    15: "DiffuseTerrain",
    16: "AdditiveMaskedEnvMetal",
}
 
BLEND_MODES = {
    0: "Opaque",
    1: "AlphaKey",
    2: "Alpha",
    3: "Add",
    4: "Mod",
    5: "Mod2x",
}
 
# ─── WMOFile ──────────────────────────────────────────────────────────────────
 
class WMOFile:
    def __init__(self):
        self.path           = None
        self.chunk_list     = []      # [[magic:bytes, data:bytearray], ...]  (ordre préservé)
        self.magic_reversed = True    # True = magics inversés (standard WMO)
        self.version        = 17
        self.is_fdid        = False   # True si Legion+ (FileDataIDs dans MOMT)
        self.n_groups       = 0
        self.motx           = bytearray()
        self.materials      = []
 
    # ── Chargement ───────────────────────────────────────────────────────────
 
    def load(self, path: str) -> str:
        """Charge et parse le fichier WMO root. Retourne '' ou un message d'erreur."""
        self.path = path
        raw = Path(path).read_bytes()
 
        # Détection de l'ordre des magics sur le premier chunk (toujours MVER)
        if len(raw) < 4:
            return "Fichier trop court."
        first4 = raw[:4]
        if first4 == b'REVM':          # MVER inversé → convention standard WMO
            self.magic_reversed = True
        elif first4 == b'MVER':        # MVER direct → rare, mais géré
            self.magic_reversed = False
        else:
            return (f"Magic inconnu : {first4!r}\n"
                    "Ce fichier ne semble pas être un WMO valide.")
 
        err = self._parse_chunks(raw)
        if err: return err
        self._parse_mohd()
        self._parse_motx()
        self._parse_momt()
        return ""
 
    def _mk(self, name: str) -> bytes:
        """Retourne le magic bytes selon la convention détectée (inversé ou non)."""
        b = name.encode('ascii')
        return b[::-1] if self.magic_reversed else b
 
    def _parse_chunks(self, raw: bytes) -> str:
        pos = 0
        self.chunk_list = []
        has_mohd = False
        mohd_magic = self._mk('MOHD')
        while pos + 8 <= len(raw):
            magic = raw[pos:pos+4]
            size  = ru32(raw, pos+4)
            if pos + 8 + size > len(raw):
                return f"Chunk {magic!r} tronqué à l'offset 0x{pos:X}"
            self.chunk_list.append([magic, bytearray(raw[pos+8: pos+8+size])])
            if magic == mohd_magic:
                has_mohd = True
            pos += 8 + size
        if not has_mohd:
            return ("Chunk MOHD absent — ce fichier n'est pas un WMO root valide.\n"
                    "Remarque : les fichiers groupe (*_NNN.wmo) ne sont pas supportés.")
        return ""
 
    def _get_chunk(self, name: str) -> bytearray:
        magic = self._mk(name)
        for m, d in self.chunk_list:
            if m == magic: return d
        return bytearray()
 
    def _set_chunk(self, name: str, data: bytearray):
        magic = self._mk(name)
        for entry in self.chunk_list:
            if entry[0] == magic:
                entry[1] = data
                return
        # Chunk absent → insérer après MOHD
        mohd_magic = self._mk('MOHD')
        for i, (m, _) in enumerate(self.chunk_list):
            if m == mohd_magic:
                self.chunk_list.insert(i+1, [magic, data])
                return
        self.chunk_list.append([magic, data])
 
    def _parse_mohd(self):
        d = self._get_chunk('MOHD')
        if len(d) < 64: return
        # MOHD layout (64 bytes) :
        #   0x00 nTextures, 0x04 nGroups, 0x08 nPortals, 0x0C nLights,
        #   0x10 nDoodadNames, 0x14 nDoodadDefs, 0x18 nDoodadSets,
        #   0x1C ambColor, 0x20 wmoID,
        #   0x24 bbox_min (3×float), 0x30 bbox_max (3×float),
        #   0x3C flags (uint16), 0x3E numLod (uint16)
        self.n_groups = ru32(d, 0x04)
        flags = struct.unpack_from('<H', d, 0x3C)[0]
        self.is_fdid  = bool(flags & MOHD_FLAG_FDID)
 
    def _parse_motx(self):
        self.motx = bytearray(self._get_chunk('MOTX'))
 
    def _parse_momt(self):
        d = self._get_chunk('MOMT')
        self.materials = []
        for i in range(len(d) // MOMT_SIZE):
            base = i * MOMT_SIZE
            if base + MOMT_SIZE > len(d): break
            flags  = ru32(d, base + 0x00)
            shader = ru32(d, base + 0x04)
            blend  = ru32(d, base + 0x08)
            tv1    = ru32(d, base + MOMT_TEX1)
            tv2    = ru32(d, base + MOMT_TEX2)
            tv3    = ru32(d, base + MOMT_TEX3)
 
            if self.is_fdid:
                t1p = t2p = t3p = ""
                f1, f2, f3 = tv1, tv2, tv3
            else:
                t1p = str_at(self.motx, tv1) if self.motx else ""
                t2p = str_at(self.motx, tv2) if self.motx else ""
                t3p = str_at(self.motx, tv3) if self.motx else ""
                f1 = f2 = f3 = 0
 
            self.materials.append({
                'index':  i,
                'flags':  flags,
                'shader': shader,
                'blend':  blend,
                'tex1_path': t1p, 'tex1_offset': tv1, 'tex1_fdid': f1,
                'tex2_path': t2p, 'tex2_offset': tv2, 'tex2_fdid': f2,
                'tex3_path': t3p, 'tex3_offset': tv3, 'tex3_fdid': f3,
            })
 
    # ── Écriture ─────────────────────────────────────────────────────────────
 
    def write_material(self, idx: int,
                       t1: str, t2: str, t3: str,
                       f1: int, f2: int, f3: int):
        """Met à jour les 3 slots texture d'un matériau."""
        if idx >= len(self.materials): return
        m = self.materials[idx]
        if self.is_fdid:
            m['tex1_fdid'] = f1
            m['tex2_fdid'] = f2
            m['tex3_fdid'] = f3
        else:
            m['tex1_path'] = t1
            m['tex2_path'] = t2
            m['tex3_path'] = t3
 
    def save(self, dest: str):
        """Reconstruit MOTX+MOMT et sauvegarde le fichier."""
        if not self.is_fdid:
            new_motx, n_tex = self._build_motx()   # met à jour les offsets dans self.materials
            self._set_chunk('MOTX', new_motx)
            # Mettre à jour MOHD.nTextures (offset 0x00)
            mohd = self._get_chunk('MOHD')
            if len(mohd) >= 4:
                wu32(mohd, 0x00, n_tex)
 
        new_momt = self._build_momt()
        self._set_chunk('MOMT', new_momt)
 
        out = bytearray()
        for magic, data in self.chunk_list:
            out.extend(magic)
            out.extend(struct.pack('<I', len(data)))
            out.extend(data)
        Path(dest).write_bytes(bytes(out))
 
    def _build_motx(self) -> tuple:
        """
        Reconstruit le pool MOTX depuis les chemins courants.
        Met à jour tex*_offset dans self.materials.
        Retourne (nouveau_motx: bytearray, n_textures: int).
        """
        new_motx     = bytearray(b'\x00')   # offset 0 = "aucune texture" (null byte)
        path_to_off  = {'': 0}
        n_tex        = 0
 
        def get_off(p: str) -> int:
            nonlocal n_tex
            if p in path_to_off:
                return path_to_off[p]
            off = len(new_motx)
            new_motx.extend(p.encode('ascii', errors='replace') + b'\x00')
            path_to_off[p] = off
            n_tex += 1
            return off
 
        for m in self.materials:
            m['tex1_offset'] = get_off(m['tex1_path'])
            m['tex2_offset'] = get_off(m['tex2_path'])
            m['tex3_offset'] = get_off(m['tex3_path'])
 
        # Alignement 4 bytes
        while len(new_motx) % 4:
            new_motx += b'\x00'
 
        return new_motx, n_tex
 
    def _build_momt(self) -> bytearray:
        """
        Copie l'original MOMT et met à jour uniquement les champs texture.
        Tous les autres champs (flags, shader, blend, colors…) sont préservés.
        """
        momt = bytearray(self._get_chunk('MOMT'))
        for m in self.materials:
            base = m['index'] * MOMT_SIZE
            if base + MOMT_SIZE > len(momt): break
            if self.is_fdid:
                wu32(momt, base + MOMT_TEX1, m['tex1_fdid'])
                wu32(momt, base + MOMT_TEX2, m['tex2_fdid'])
                wu32(momt, base + MOMT_TEX3, m['tex3_fdid'])
            else:
                wu32(momt, base + MOMT_TEX1, m['tex1_offset'])
                wu32(momt, base + MOMT_TEX2, m['tex2_offset'])
                wu32(momt, base + MOMT_TEX3, m['tex3_offset'])
        return momt
 
 
# ─── UI ──────────────────────────────────────────────────────────────────────
 
COLOR_BG      = "#1a1a1a"
COLOR_SURFACE = "#222222"
COLOR_PANEL   = "#1e1e1e"
COLOR_SEP     = "#333333"
COLOR_TEXT    = "#cccccc"
COLOR_DIM     = "#666666"
COLOR_ACCENT  = "#4A90D9"
COLOR_ORANGE  = "#BA7517"
COLOR_GREEN   = "#1D9E75"
 
 
class WMOTextureEditor(TkinterDnD.Tk if HAS_DND else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WMO Texture Editor")
        self.configure(bg=COLOR_BG)
        self.resizable(True, True)
        self.minsize(860, 400)
        self.geometry("1100x620")
 
        self.wmo      = None
        self.mat_rows = []
        self._build_ui()
 
        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
 
    # ── Construction UI ───────────────────────────────────────────────────────
 
    def _build_ui(self):
        # Barre titre
        top = tk.Frame(self, bg=COLOR_BG, padx=12, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="WMO Texture Editor", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 13, "bold")).pack(side=tk.LEFT)
        tk.Button(top, text="Charger WMO…", command=self._load,
                  bg=COLOR_ACCENT, fg="#fff", relief=tk.FLAT,
                  font=("Consolas", 9, "bold"), padx=12, pady=4,
                  cursor="hand2", bd=0, activebackground="#2a6db5"
                  ).pack(side=tk.RIGHT)
        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)
 
        # Infos fichier
        self.info_frame = tk.Frame(self, bg=COLOR_PANEL, padx=12, pady=6)
        self.info_frame.pack(fill=tk.X)
        hint = ("  ↓  Glisse un fichier .wmo (root) ici ou utilise le bouton Charger"
                if HAS_DND else "  Aucun fichier chargé — utilise le bouton Charger")
        self.lbl_info = tk.Label(self.info_frame, text=hint, bg=COLOR_PANEL,
                                  fg=COLOR_DIM, font=("Consolas", 9), anchor="w")
        self.lbl_info.pack(fill=tk.X)
        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)
 
        # Zone scrollable des matériaux
        outer = tk.Frame(self, bg=COLOR_BG)
        outer.pack(fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(outer, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas = tk.Canvas(outer, bg=COLOR_BG, highlightthickness=0,
                                yscrollcommand=sb.set)
        sb.config(command=self.canvas.yview)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.mat_frame = tk.Frame(self.canvas, bg=COLOR_BG)
        self._cwin = self.canvas.create_window((0,0), window=self.mat_frame, anchor="nw")
        self.mat_frame.bind("<Configure>",
                            lambda e: self.canvas.configure(
                                scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self._cwin, width=e.width))
        self.canvas.bind("<MouseWheel>",
                         lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(+1, "units"))
 
        # Barre boutons bas
        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)
        bot = tk.Frame(self, bg=COLOR_BG, padx=10, pady=8)
        bot.pack(fill=tk.X)
        self.btn_save = tk.Button(bot, text="Enregistrer", command=self._save,
                                   bg="#1a5c38", fg="#fff", relief=tk.FLAT,
                                   font=("Consolas", 9, "bold"), padx=12, pady=4,
                                   cursor="hand2", bd=0, activebackground="#144d2f",
                                   state=tk.DISABLED)
        self.btn_save.pack(side=tk.LEFT, padx=(0,6))
        self.btn_saveas = tk.Button(bot, text="Enregistrer sous…", command=self._save_as,
                                     bg=COLOR_ORANGE, fg="#fff", relief=tk.FLAT,
                                     font=("Consolas", 9, "bold"), padx=12, pady=4,
                                     cursor="hand2", bd=0, activebackground="#9a6010",
                                     state=tk.DISABLED)
        self.btn_saveas.pack(side=tk.LEFT)
        self.lbl_status = tk.Label(bot, text="", bg=COLOR_BG, fg=COLOR_DIM,
                                    font=("Consolas", 9))
        self.lbl_status.pack(side=tk.RIGHT, padx=8)
 
    # ── Drag & Drop ───────────────────────────────────────────────────────────
 
    def _on_drop(self, event):
        raw = event.data.strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        path = raw.split("} {")[0].strip("{}")
        if path.lower().endswith(".wmo"):
            self._load(path)
        else:
            messagebox.showwarning("Format non supporté",
                                   "Seuls les fichiers .wmo root sont acceptés.\n"
                                   "(Pas les fichiers groupe *_NNN.wmo)")
 
    # ── Chargement ────────────────────────────────────────────────────────────
 
    def _load(self, path=None):
        if not path:
            path = filedialog.askopenfilename(
                title="Ouvrir un fichier WMO root",
                filetypes=[("WMO files", "*.wmo *.WMO"), ("All files", "*.*")])
        if not path: return
 
        wmo = WMOFile()
        err = wmo.load(path)
        if err:
            messagebox.showerror("Erreur de lecture", err)
            return
        self.wmo = wmo
        self._refresh_ui()
 
    def _refresh_ui(self):
        w = self.wmo
        if not w: return
 
        mode = "Legion+ — FileDataIDs" if w.is_fdid else "pré-Legion — chemins MOTX"
        self.lbl_info.config(
            text=f"{Path(w.path).name}   |   WMO root v{w.version}   |   {mode}   |   "
                 f"{len(w.materials)} matériau(x)   |   {w.n_groups} groupe(s)",
            fg=COLOR_TEXT)
 
        for widget in self.mat_frame.winfo_children():
            widget.destroy()
        self.mat_rows = []
 
        if not w.materials:
            tk.Label(self.mat_frame, text="Aucun matériau (MOMT vide ou absent).",
                     bg=COLOR_BG, fg=COLOR_DIM, font=("Consolas", 9),
                     padx=16, pady=20).pack(anchor="w")
            self._enable_buttons()
            return
 
        # Header colonnes (adapté selon le mode)
        if w.is_fdid:
            col1 = "FDID Tex1 (Diffuse)"
            col2 = "FDID Tex2 (Specular)"
            col3 = "FDID Tex3"
        else:
            col1 = "Tex1 — Diffuse (chemin MOTX)"
            col2 = "Tex2 — Specular"
            col3 = "Tex3"
 
        hdr = tk.Frame(self.mat_frame, bg=COLOR_PANEL, padx=10, pady=5)
        hdr.pack(fill=tk.X)
        for txt, w_ in [("#", 3), ("Shader", 20), ("Blend", 10),
                        (col1, 32), (col2, 28), (col3, 18)]:
            tk.Label(hdr, text=txt, bg=COLOR_PANEL, fg=COLOR_DIM,
                     font=("Consolas", 8, "bold"), width=w_, anchor="w"
                     ).pack(side=tk.LEFT, padx=2)
 
        tk.Frame(self.mat_frame, bg=COLOR_SEP, height=1).pack(fill=tk.X)
 
        for mat in w.materials:
            self._build_mat_row(mat)
 
        self._enable_buttons()
 
    def _build_mat_row(self, mat: dict):
        row = tk.Frame(self.mat_frame, bg=COLOR_BG, pady=5, padx=10)
        row.pack(fill=tk.X)
        tk.Frame(self.mat_frame, bg=COLOR_SEP, height=1).pack(fill=tk.X)
 
        i      = mat['index']
        shader = WMO_SHADERS.get(mat['shader'], f"unk({mat['shader']})")
        blend  = BLEND_MODES.get(mat['blend'],  f"unk({mat['blend']})")
 
        tk.Label(row, text=str(i), bg=COLOR_BG, fg=COLOR_DIM,
                 font=("Consolas", 9), width=3, anchor="w").pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=shader, bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 9), width=20, anchor="w").pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=blend, bg=COLOR_BG, fg=COLOR_DIM,
                 font=("Consolas", 9), width=10, anchor="w").pack(side=tk.LEFT, padx=2)
 
        is_fdid = self.wmo.is_fdid
        vars_path = []
        vars_fdid = []
 
        # Widths des champs : (path_w, fdid_w) par slot
        slot_widths = [(32, 16), (28, 14), (18, 12)]
 
        for slot_idx, ((pk, fk), (pw, fw)) in enumerate(zip(
            [('tex1_path','tex1_fdid'), ('tex2_path','tex2_fdid'), ('tex3_path','tex3_fdid')],
            slot_widths
        )):
            if is_fdid:
                vp = tk.StringVar(value="")
                vf = tk.StringVar(value=str(mat[fk]) if mat[fk] else "")
                tk.Entry(row, textvariable=vf,
                         bg=COLOR_SURFACE, fg=COLOR_ACCENT,
                         insertbackground=COLOR_ACCENT,
                         relief=tk.FLAT, font=("Consolas", 9), width=fw
                         ).pack(side=tk.LEFT, padx=3)
            else:
                vp = tk.StringVar(value=mat[pk])
                vf = tk.StringVar(value="")
                tk.Entry(row, textvariable=vp,
                         bg=COLOR_SURFACE, fg=COLOR_TEXT,
                         insertbackground=COLOR_TEXT,
                         relief=tk.FLAT, font=("Consolas", 9), width=pw
                         ).pack(side=tk.LEFT, padx=3)
            vars_path.append(vp)
            vars_fdid.append(vf)
 
        # Bouton ✓ par ligne
        def apply_row(idx=i, vp=vars_path, vf=vars_fdid):
            paths = [v.get().strip() for v in vp]
            fdid_strs = [v.get().strip() for v in vf]
            fdids = []
            for s in fdid_strs:
                if s:
                    try:
                        fdids.append(int(s))
                    except ValueError:
                        messagebox.showwarning("FDID invalide",
                                               f"Le FileDataID doit être un entier.\nValeur : '{s}'")
                        return
                else:
                    fdids.append(0)
            self.wmo.write_material(idx, paths[0], paths[1], paths[2],
                                    fdids[0], fdids[1], fdids[2])
            self.lbl_status.config(text=f"Matériau [{idx}] mis à jour", fg=COLOR_GREEN)
            self.after(3000, lambda: self.lbl_status.config(text=""))
 
        tk.Button(row, text="✓", command=apply_row,
                  bg=COLOR_GREEN, fg="#fff", relief=tk.FLAT,
                  font=("Consolas", 9, "bold"), padx=6, pady=2,
                  cursor="hand2", bd=0, activebackground="#15785a"
                  ).pack(side=tk.LEFT, padx=4)
 
        self.mat_rows.append({'mat': mat, 'vars_path': vars_path, 'vars_fdid': vars_fdid})
 
    # ── Sauvegarde ────────────────────────────────────────────────────────────
 
    def _apply_all(self):
        """Applique tous les champs avant sauvegarde."""
        for row in self.mat_rows:
            mat   = row['mat']
            paths = [v.get().strip() for v in row['vars_path']]
            fdids = [int(v.get().strip()) if v.get().strip().isdigit() else 0
                     for v in row['vars_fdid']]
            self.wmo.write_material(mat['index'],
                                    paths[0], paths[1], paths[2],
                                    fdids[0], fdids[1], fdids[2])
 
    def _save(self):
        if not self.wmo: return
        self._apply_all()
        bak = self.wmo.path + '.bak'
        shutil.copy2(self.wmo.path, bak)
        self.wmo.save(self.wmo.path)
        self.lbl_status.config(
            text=f"Sauvegardé  (backup : {Path(bak).name})", fg=COLOR_GREEN)
 
    def _save_as(self):
        if not self.wmo: return
        self._apply_all()
        dest = filedialog.asksaveasfilename(
            title="Enregistrer sous…",
            initialfile=Path(self.wmo.path).name,
            defaultextension=".wmo",
            filetypes=[("WMO files", "*.wmo"), ("All files", "*.*")])
        if not dest: return
        self.wmo.save(dest)
        self.lbl_status.config(text=f"Sauvegardé : {Path(dest).name}", fg=COLOR_GREEN)
 
    def _enable_buttons(self):
        state = tk.NORMAL if self.wmo else tk.DISABLED
        self.btn_save.config(state=state)
        self.btn_saveas.config(state=state)
 
 
# ─── Entry point ─────────────────────────────────────────────────────────────
 
if __name__ == '__main__':
    app = WMOTextureEditor()
    if len(sys.argv) > 1:
        app.after(100, lambda: app._load(sys.argv[1]))
    app.mainloop()