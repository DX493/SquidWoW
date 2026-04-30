#!/usr/bin/env python3
"""
M2 Texture Editor
Permet de visualiser et modifier les chemins de texture et FileDataIDs
pour tous les formats M2 connus (Vanilla → Retail/Midnight).
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
def ri32(d, o): return struct.unpack_from('<i', d, o)[0]
def ru16(d, o): return struct.unpack_from('<H', d, o)[0]
def wu32(b, o, v): struct.pack_into('<I', b, o, v)

def str_at(data, offset, maxlen=512):
    if offset <= 0 or offset >= len(data): return ""
    end = offset
    while end < len(data) and end < offset + maxlen and data[end] != 0:
        end += 1
    return data[offset:end].decode('utf-8', errors='replace')

def get_expansion(v):
    if v >= 274: return "Legion+"
    if v >= 273: return "WoD"
    if v >= 272: return "MoP"
    if v >= 265: return "Cata"
    if v >= 264: return "WotLK"
    if v >= 260: return "TBC"
    return "Vanilla"

TEX_TYPES = {
    0:  "none / hardcoded",
    1:  "skin",
    2:  "object_skin",
    3:  "weapon_blade",
    4:  "weapon_handle",
    5:  "environment",
    6:  "char_hair",
    7:  "char_facial_hair",
    8:  "skin_extra",
    9:  "ui_skin",
    11: "tallstrider_wing",
    12: "monster_1",
    13: "monster_2",
    14: "monster_3",
}

# ─── Parsing M2 ───────────────────────────────────────────────────────────────

class M2File:
    def __init__(self):
        self.path       = None
        self.raw        = None        # bytes originaux du fichier
        self.is_md21    = False
        self.md20_data  = None        # bytearray des données MD20
        self.md20_start = 0           # offset du début MD20 dans raw (8 pour MD21)
        self.version    = 0
        self.xpac       = ""
        self.textures   = []          # liste de dicts
        self.txids      = []          # FileDataIDs depuis chunk TXID
        self.sfids      = []          # Skin FileDataIDs depuis chunk SFID
        self.extra_chunks = []        # chunks autres que MD21

    def load(self, path: str) -> str:
        """Charge et parse le fichier. Retourne un message d'erreur ou ''."""
        self.path = path
        raw = Path(path).read_bytes()
        self.raw = raw
        magic = raw[:4]

        if magic == b'MD21':
            self.is_md21 = True
            self._parse_chunks(raw)
        elif magic == b'MD20':
            self.is_md21 = False
            self.md20_data  = bytearray(raw)
            self.md20_start = 0
        else:
            return f"Magic inconnu : {magic!r}"

        self._parse_md20()
        return ""

    def _parse_chunks(self, raw: bytes):
        """Parse les chunks d'un fichier MD21."""
        pos = 0
        self.extra_chunks = []
        while pos + 8 <= len(raw):
            cm   = raw[pos:pos+4]
            size = ru32(raw, pos+4)
            if cm == b'MD21':
                self.md20_data  = bytearray(raw[pos+8 : pos+8+size])
                self.md20_start = pos + 8
            elif cm == b'TXID':
                self.txids = [ru32(raw, pos+8+i*4) for i in range(size//4)]
            elif cm == b'SFID':
                self.sfids = [ru32(raw, pos+8+i*4) for i in range(size//4)]
            else:
                self.extra_chunks.append((cm, raw[pos+8:pos+8+size]))
            pos += 8 + size

    def _tex_array_offset(self) -> tuple:
        """
        Retourne (count, offset) du tableau de textures selon la version.
        Le header M2 a évolué — l'offset du champ textures change selon la version.

        Layout vanilla (v256) :
          0x08/0x0C : name
          0x10/0x14 : global sequences
          0x18/0x1C : animations
          0x20/0x24 : bones
          0x28/0x2C : key_bone_lookup
          0x30/0x34 : vertices
          0x38      : nViews (scalar)
          0x3C/0x40 : colors
          0x44/0x48 : textures  ← count/offset vanilla
          ...

        Layout WotLK+ (v264+) :
          0x50/0x54 : textures  ← count/offset WotLK+
        """
        d = self.md20_data
        v = self.version
        if v < 260:
            # Vanilla/TBC : textures à 0x44/0x48 — mais layout est (offset,count)
            # Confirmé par analyse binaire : oTextures=0x60, nTextures=2
            # En vanilla le layout RÉEL confirmé par les données :
            # 0x5C/0x60 = replaceable textures (count=2, offset=0x5910)
            # En fait on a trouvé que les textures sont à offset 0x60 (count=1, offset=0x5910)
            # Réessayons avec le vrai layout vanilla :
            # 0x44 = nTextures, 0x48 = oTextures
            nc = ru32(d, 0x44)
            no = ru32(d, 0x48)
            # Sanity check : nc doit être raisonnable et no doit pointer dans le fichier
            if 0 < nc < 32 and 0 < no < len(d):
                return nc, no
            # Fallback : essayer 0x5C/0x60
            nc = ru32(d, 0x5C)
            no = ru32(d, 0x60)
            if 0 < nc < 32 and 0 < no < len(d):
                return nc, no
            return 0, 0
        else:
            # WotLK+ : textures à 0x50/0x54
            return ru32(d, 0x50), ru32(d, 0x54)

    def _parse_md20(self):
        """Parse le header MD20 et extrait les textures."""
        d = self.md20_data
        self.version = ru32(d, 0x04)
        self.xpac    = get_expansion(self.version)

        tex_count, tex_offset = self._tex_array_offset()

        self.textures = []
        for i in range(min(tex_count, 64)):   # sanity cap
            base       = tex_offset + i * 16
            if base + 16 > len(d): break
            tex_type   = ru32(d, base + 0)
            tex_flags  = ru32(d, base + 4)
            name_count = ru32(d, base + 8)
            name_offset= ru32(d, base + 12)
            # Sanity check sur les valeurs
            if name_count > 512 or (name_offset > 0 and name_offset >= len(d)):
                break
            filename   = str_at(d, name_offset, name_count) if name_count > 0 and name_offset > 0 else ""
            txid       = self.txids[i] if i < len(self.txids) else 0
            self.textures.append({
                'index':       i,
                'type':        tex_type,
                'flags':       tex_flags,
                'name_count':  name_count,
                'name_offset': name_offset,
                'filename':    filename,
                'txid':        txid,
            })

    def write_texture(self, index: int, new_path: str, new_txid: int = None):
        """
        Écrit le chemin de texture [index] dans md20_data.
        Si new_txid est fourni, met aussi à jour txids.
        """
        if index >= len(self.textures):
            return

        tex       = self.textures[index]
        d         = self.md20_data
        tex_offset= ru32(d, 0x54)
        base      = tex_offset + index * 16
        name_field= base + 8

        if new_path != tex['filename']:
            path_bytes = new_path.encode('utf-8') + b'\x00'
            path_len   = len(path_bytes)
            cur_offset = tex['name_offset']

            if cur_offset > 0 and cur_offset + path_len <= len(d):
                # Écrire en place (zone réservée existante)
                d[cur_offset : cur_offset + path_len] = path_bytes
                # Effacer le reste de l'ancien chemin
                old_end = cur_offset + tex['name_count']
                if old_end > cur_offset + path_len:
                    d[cur_offset + path_len : old_end] = b'\x00' * (old_end - cur_offset - path_len)
                wu32(d, name_field, path_len)
            else:
                # Appender en fin de fichier
                new_offset = len(d)
                self.md20_data = bytearray(d) + path_bytes
                while len(self.md20_data) % 4 != 0:
                    self.md20_data += b'\x00'
                wu32(self.md20_data, name_field,     path_len)
                wu32(self.md20_data, name_field + 4, new_offset)

            tex['filename']   = new_path
            tex['name_count'] = path_len

        if new_txid is not None and new_txid != tex['txid']:
            tex['txid'] = new_txid
            if index < len(self.txids):
                self.txids[index] = new_txid
            else:
                while len(self.txids) <= index:
                    self.txids.append(0)
                self.txids[index] = new_txid

    def save(self, path: str):
        """Reconstruit et sauvegarde le fichier."""
        if self.is_md21:
            # Reconstruire MD21 : MD21 chunk + chunks extras + SFID + TXID
            md20_bytes = bytes(self.md20_data)
            out = bytearray()

            def write_chunk(magic: bytes, data: bytes):
                out.extend(magic)
                out.extend(struct.pack('<I', len(data)))
                out.extend(data)

            write_chunk(b'MD21', md20_bytes)

            for cm, cdata in self.extra_chunks:
                write_chunk(cm, cdata)

            if self.sfids:
                sfid_data = b''.join(struct.pack('<I', x) for x in self.sfids)
                write_chunk(b'SFID', sfid_data)

            if self.txids:
                txid_data = b''.join(struct.pack('<I', x) for x in self.txids)
                write_chunk(b'TXID', txid_data)

            Path(path).write_bytes(bytes(out))
        else:
            Path(path).write_bytes(bytes(self.md20_data))


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

class M2TextureEditor(TkinterDnD.Tk if HAS_DND else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("M2 Texture Editor")
        self.configure(bg=COLOR_BG)
        self.resizable(True, True)
        self.minsize(720, 400)
        self.geometry("860x560")

        self.m2 = None
        self.tex_rows = []
        self._build_ui()

        # Drag & drop sur la fenêtre entière
        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)

    # ── Construction UI ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Barre titre
        top = tk.Frame(self, bg=COLOR_BG, padx=12, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="M2 Texture Editor", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 13, "bold")).pack(side=tk.LEFT)

        btn_cfg = dict(bg="#2a2a2a", fg=COLOR_TEXT, relief=tk.FLAT,
                       font=("Consolas", 9), padx=10, pady=4,
                       cursor="hand2", bd=0, activebackground="#333")
        tk.Button(top, text="Charger M2…", command=self._load,
                  bg=COLOR_ACCENT, fg="#fff", relief=tk.FLAT,
                  font=("Consolas", 9, "bold"), padx=12, pady=4,
                  cursor="hand2", bd=0, activebackground="#2a6db5"
                  ).pack(side=tk.RIGHT)

        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)

        # Infos fichier
        self.info_frame = tk.Frame(self, bg=COLOR_PANEL, padx=12, pady=6)
        self.info_frame.pack(fill=tk.X)
        hint = "  ↓  Glisse un fichier .m2 ici ou utilise le bouton Charger" if HAS_DND else "  Aucun fichier chargé — utilise le bouton Charger"
        self.lbl_info = tk.Label(self.info_frame,
                                  text=hint,
                                  bg=COLOR_PANEL, fg=COLOR_DIM,
                                  font=("Consolas", 9), anchor="w")
        self.lbl_info.pack(fill=tk.X)

        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)

        # Zone scrollable des textures
        outer = tk.Frame(self, bg=COLOR_BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        sb = tk.Scrollbar(outer, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas = tk.Canvas(outer, bg=COLOR_BG, highlightthickness=0,
                                yscrollcommand=sb.set)
        sb.config(command=self.canvas.yview)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.tex_frame = tk.Frame(self.canvas, bg=COLOR_BG)
        self._canvas_win = self.canvas.create_window((0,0), window=self.tex_frame, anchor="nw")
        self.tex_frame.bind("<Configure>",
                            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self._canvas_win, width=e.width))
        self.canvas.bind("<MouseWheel>",
                         lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(+1, "units"))

        # Barre boutons bas
        tk.Frame(self, bg=COLOR_SEP, height=1).pack(fill=tk.X)
        bot = tk.Frame(self, bg=COLOR_BG, padx=10, pady=8)
        bot.pack(fill=tk.X)
        self.btn_save = tk.Button(bot, text="Enregistrer",
                                   command=self._save,
                                   bg="#1a5c38", fg="#fff", relief=tk.FLAT,
                                   font=("Consolas", 9, "bold"), padx=12, pady=4,
                                   cursor="hand2", bd=0, activebackground="#144d2f",
                                   state=tk.DISABLED)
        self.btn_save.pack(side=tk.LEFT, padx=(0,6))
        self.btn_save_as = tk.Button(bot, text="Enregistrer sous…",
                                      command=self._save_as,
                                      bg=COLOR_ORANGE, fg="#fff", relief=tk.FLAT,
                                      font=("Consolas", 9, "bold"), padx=12, pady=4,
                                      cursor="hand2", bd=0, activebackground="#9a6010",
                                      state=tk.DISABLED)
        self.btn_save_as.pack(side=tk.LEFT)
        self.lbl_status = tk.Label(bot, text="", bg=COLOR_BG, fg=COLOR_DIM,
                                    font=("Consolas", 9))
        self.lbl_status.pack(side=tk.RIGHT, padx=8)

    # ── Drag & Drop ───────────────────────────────────────────────────────────

    def _on_drop(self, event):
        """Gère le drop d'un fichier .m2 depuis l'explorateur."""
        raw = event.data.strip()
        # Tkinter DnD peut wrapper le chemin entre accolades si espaces
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        # Prendre le premier fichier si plusieurs droppés
        path = raw.split("} {")[0].strip("{}")
        if path.lower().endswith(".m2"):
            self._load(path)
        else:
            messagebox.showwarning("Format non supporté",
                                   "Seuls les fichiers .m2 sont acceptés.")

    # ── Chargement ────────────────────────────────────────────────────────────

    def _load(self, path=None):
        if not path:
            path = filedialog.askopenfilename(
                title="Ouvrir un fichier M2",
                filetypes=[("M2 files", "*.m2 *.M2"), ("All files", "*.*")])
        if not path: return

        m2 = M2File()
        err = m2.load(path)
        if err:
            messagebox.showerror("Erreur", f"Impossible de lire le fichier :\n{err}")
            return

        self.m2 = m2
        self._refresh_ui()

    def _refresh_ui(self):
        m2 = self.m2
        if not m2: return

        fmt = "MD21 (Legion+ chunked)" if m2.is_md21 else "MD20 (pre-Legion flat)"
        fname = Path(m2.path).name
        self.lbl_info.config(
            text=f"{fname}   |   {fmt}   |   version={m2.version} ({m2.xpac})   |   "
                 f"{len(m2.textures)} texture(s)",
            fg=COLOR_TEXT)

        # Vider le frame textures
        for w in self.tex_frame.winfo_children():
            w.destroy()
        self.tex_rows = []

        if not m2.textures:
            tk.Label(self.tex_frame, text="Aucune texture dans ce fichier.",
                     bg=COLOR_BG, fg=COLOR_DIM, font=("Consolas", 9),
                     padx=16, pady=20).pack(anchor="w")
            self._enable_buttons()
            return

        # Header colonnes
        hdr = tk.Frame(self.tex_frame, bg=COLOR_PANEL, padx=10, pady=5)
        hdr.pack(fill=tk.X)
        for txt, w in [("#", 3), ("Type", 22), ("Flags", 8),
                       ("Chemin de texture", 40), ("FileDataID (TXID)", 16)]:
            tk.Label(hdr, text=txt, bg=COLOR_PANEL, fg=COLOR_DIM,
                     font=("Consolas", 8, "bold"), width=w, anchor="w"
                     ).pack(side=tk.LEFT, padx=2)

        tk.Frame(self.tex_frame, bg=COLOR_SEP, height=1).pack(fill=tk.X)

        # Lignes textures
        for tex in m2.textures:
            self._build_tex_row(tex)

        self._enable_buttons()

    def _build_tex_row(self, tex: dict):
        row = tk.Frame(self.tex_frame, bg=COLOR_BG, pady=6, padx=10)
        row.pack(fill=tk.X)
        tk.Frame(self.tex_frame, bg=COLOR_SEP, height=1).pack(fill=tk.X)

        i    = tex['index']
        typ  = tex['type']
        flg  = tex['flags']
        typ_name = TEX_TYPES.get(typ, f"unk({typ})")

        tk.Label(row, text=str(i), bg=COLOR_BG, fg=COLOR_DIM,
                 font=("Consolas", 9), width=3, anchor="w").pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=f"{typ_name}", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Consolas", 9), width=22, anchor="w").pack(side=tk.LEFT, padx=2)
        tk.Label(row, text=f"0x{flg:04X}", bg=COLOR_BG, fg=COLOR_DIM,
                 font=("Consolas", 9), width=8, anchor="w").pack(side=tk.LEFT, padx=2)

        # Champ chemin
        var_path = tk.StringVar(value=tex['filename'])
        entry_path = tk.Entry(row, textvariable=var_path,
                               bg=COLOR_SURFACE, fg=COLOR_TEXT,
                               insertbackground=COLOR_TEXT,
                               relief=tk.FLAT, font=("Consolas", 9),
                               width=42)
        entry_path.pack(side=tk.LEFT, padx=4)

        # Champ TXID (seulement si MD21 ou txid non nul)
        var_txid = tk.StringVar(value=str(tex['txid']) if tex['txid'] else "")
        entry_txid = tk.Entry(row, textvariable=var_txid,
                               bg=COLOR_SURFACE, fg=COLOR_ACCENT,
                               insertbackground=COLOR_ACCENT,
                               relief=tk.FLAT, font=("Consolas", 9),
                               width=16)
        entry_txid.pack(side=tk.LEFT, padx=4)

        # Bouton appliquer
        def apply_row(idx=i, vp=var_path, vt=var_txid):
            new_path = vp.get().strip()
            txid_str = vt.get().strip()
            new_txid = None
            if txid_str:
                try:
                    new_txid = int(txid_str)
                except ValueError:
                    messagebox.showwarning("TXID invalide",
                                           f"Le FileDataID doit être un entier.\nValeur : '{txid_str}'")
                    return
            self.m2.write_texture(idx, new_path, new_txid)
            self.lbl_status.config(text=f"Texture [{idx}] mise à jour", fg=COLOR_GREEN)
            self.after(3000, lambda: self.lbl_status.config(text=""))

        tk.Button(row, text="✓", command=apply_row,
                  bg=COLOR_GREEN, fg="#fff", relief=tk.FLAT,
                  font=("Consolas", 9, "bold"), padx=6, pady=2,
                  cursor="hand2", bd=0, activebackground="#15785a"
                  ).pack(side=tk.LEFT, padx=4)

        self.tex_rows.append({'var_path': var_path, 'var_txid': var_txid, 'tex': tex})

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    def _save(self):
        if not self.m2: return
        # Appliquer tous les champs avant de sauvegarder
        self._apply_all()
        bak = self.m2.path + '.bak'
        shutil.copy2(self.m2.path, bak)
        self.m2.save(self.m2.path)
        self.lbl_status.config(
            text=f"Sauvegardé  (backup : {Path(bak).name})", fg=COLOR_GREEN)

    def _save_as(self):
        if not self.m2: return
        self._apply_all()
        dest = filedialog.asksaveasfilename(
            title="Enregistrer sous…",
            initialfile=Path(self.m2.path).name,
            defaultextension=".m2",
            filetypes=[("M2 files", "*.m2"), ("All files", "*.*")])
        if not dest: return
        self.m2.save(dest)
        self.lbl_status.config(text=f"Sauvegardé : {Path(dest).name}", fg=COLOR_GREEN)

    def _apply_all(self):
        """Applique tous les champs d'entrée avant sauvegarde."""
        for row in self.tex_rows:
            idx      = row['tex']['index']
            new_path = row['var_path'].get().strip()
            txid_str = row['var_txid'].get().strip()
            new_txid = int(txid_str) if txid_str.isdigit() else None
            self.m2.write_texture(idx, new_path, new_txid)

    def _enable_buttons(self):
        state = tk.NORMAL if self.m2 else tk.DISABLED
        self.btn_save.config(state=state)
        self.btn_save_as.config(state=state)


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = M2TextureEditor()
    if len(sys.argv) > 1:
        app.after(100, lambda: app._load(sys.argv[1]))
    app.mainloop()
