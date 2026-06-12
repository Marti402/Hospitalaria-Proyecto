"""
Pharma Control – Flet edition
Adaptado al esquema SQL hospi: tablas, triggers, procedimientos y flujo de datos exactos.

FLUJO CLAVE (del trigger procesar_registro_entrada):
  - INSERT en registro_entrada  → trigger hace TODO (medicamento, caducidad, stock, historial)
  - temperatura_ambiente/humedad_ambiente pueden ser NULL → el trigger solo llama a
    registrar_medicion_espacio si no son NULL
  - id_config puede ser NULL → el trigger usa el espacio seleccionado (seleccionada=TRUE)
  - Si el medicamento ya existe → UPDATE (no INSERT duplicado)
  - La validación de compatibilidad ocurre en MySQL, no en Python

Requiere: flet==0.85.0, mysql-connector-python, pyserial
"""

import asyncio
import queue
import re
import threading
import time
from datetime import datetime

import flet as ft
import mysql.connector
import serial
import serial.tools.list_ports
from mysql.connector import Error


DB_CONFIG = {
    "host":     "localhost",
    "user":     "root",
    "password": "1234",
    "database": "hospi",
}

REFRESH_MS  = 5000
SERIAL_BAUD = 9600

# Paleta dark medical
C_BG      = "#0d1117"
C_SURFACE  = "#161b22"
C_SURFACE2 = "#21262d"
C_BORDER   = "#30363d"
C_ACCENT   = "#3b82f6"
C_ACCENT2  = "#1d4ed8"
C_TEXT     = "#e6edf3"
C_MUTED    = "#8b949e"
C_OK       = "#22c55e"
C_WARN     = "#f59e0b"
C_ERR      = "#ef4444"
C_INFO     = "#38bdf8"

THEMES = {
    "dark": {
        "bg": "#0d1117", "surface": "#161b22", "surface2": "#21262d",
        "border": "#30363d", "accent": "#3b82f6", "accent2": "#1d4ed8",
        "text": "#e6edf3", "muted": "#8b949e",
    },
    "light": {
        "bg": "#f4f7fb", "surface": "#ffffff", "surface2": "#eef3f8",
        "border": "#d6dee8", "accent": "#2563eb", "accent2": "#1d4ed8",
        "text": "#111827", "muted": "#64748b",
    },
}

def set_palette(mode="dark"):
    """Actualiza la paleta global usada por los widgets nuevos y por los refrescos de tablas."""
    global C_BG, C_SURFACE, C_SURFACE2, C_BORDER, C_ACCENT, C_ACCENT2, C_TEXT, C_MUTED
    pal = THEMES[mode]
    C_BG, C_SURFACE, C_SURFACE2 = pal["bg"], pal["surface"], pal["surface2"]
    C_BORDER, C_ACCENT, C_ACCENT2 = pal["border"], pal["accent"], pal["accent2"]
    C_TEXT, C_MUTED = pal["text"], pal["muted"]


def conectar_mysql():
    return mysql.connector.connect(**DB_CONFIG)


def texto_error_mysql(error):
    """Formatea errores MySQL de forma legible."""
    if isinstance(error, Error):
        msg = getattr(error, "msg", None) or str(error)
        return msg
    return str(error)


def ejecutar_query(query, params=None, fetch=False):
    """Ejecuta una query y opcionalmente devuelve filas."""
    conn = None
    try:
        conn = conectar_mysql()
        cur  = conn.cursor()
        cur.execute(query, params or ())
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows or []
    finally:
        if conn and conn.is_connected():
            conn.close()


def ejecutar_procedure(nombre, params=()):
    """
    Ejecuta un procedimiento almacenado.
    Consume todos los result sets para evitar 'Commands out of sync'.
    """
    conn = None
    try:
        print(f"[DEBUG ejecutar_procedure] Llamando {nombre} con params {params}") 
        conn = conectar_mysql()
        cur  = conn.cursor()
        cur.callproc(nombre, params)
        for _ in cur.stored_results():
            pass
        conn.commit()
        print(f"[DEBUG ejecutar_procedure] ✓ {nombre} ejecutado OK") 
    except Exception as ex:
        print(f"[DEBUG ejecutar_procedure] ✗ ERROR en {nombre}: {ex}")
        raise
    finally:
        if conn and conn.is_connected():
            conn.close()


def get_config_activa():
    """
    Devuelve (id_config, nombre_espacio) del espacio marcado como seleccionado,
    o None si no hay ninguno.
    """
    rows = ejecutar_query(
        "SELECT id_config, nombre_espacio "
        "FROM configuracion_espacio WHERE seleccionada = TRUE LIMIT 1",
        fetch=True,
    )
    return rows[0] if rows else None

def buscar_arduino():
    """Escanea puertos COM y conecta al primer Arduino encontrado."""
    try:
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            if any(k in desc for k in ("ARDUINO", "USB SERIAL", "CH340")):
                try:
                    ser = serial.Serial(p.device, SERIAL_BAUD, timeout=0.1)
                    time.sleep(2)
                    return ser
                except Exception:
                    continue
    except Exception:
        pass
    return None


def parsear_id_barcode(linea):
    """
    Detecta códigos de barras recibidos por Serial.

    Acepta como la misma ID:
      - ID:8800141005689
      - 8800141005689

    Devuelve siempre el código limpio, sin el prefijo ID:, o None si la línea
    no corresponde a un código de barras.
    """
    if not linea:
        return None

    linea = linea.strip()
    if not linea:
        return None

    if linea.upper().startswith("DATA:"):
        return None

    # Caso 1: Arduino envía ID:8800141005689
    if linea.upper().startswith("ID:"):
        codigo = linea[3:].strip()
        return codigo if codigo else None

    # Caso 2: llega solo el código, por ejemplo 8800141005689.
    # Se limita a una cadena compacta de código de barras para no interpretar
    # mensajes del terminal como IDs.
    if re.fullmatch(r"[0-9A-Za-z_-]{4,}", linea):
        return linea

    return None

def parsear_sensor(linea):
    """
    Parsea líneas del Arduino con formatos aceptados:

      DATA:21.00,50.00
      @21.00/50.00

    Devuelve (temp_float, hum_float) o None.
    """
    if not linea:
        return None

    linea = linea.strip()

    if not linea:
        return None

    try:
        if linea.upper().startswith("DATA:"):
            payload = linea[5:].strip()
            temp_txt, hum_txt = payload.split(",", 1)
            return float(temp_txt.strip()), float(hum_txt.strip())

        if linea.startswith("@"):
            payload = linea[1:].strip()
            temp_txt, hum_txt = payload.split("/", 1)
            return float(temp_txt.strip()), float(hum_txt.strip())

        return None

    except (ValueError, TypeError):
        return None


def mk_field(label, hint="", width=200):
    """Devuelve (Column-label+field, TextField). El TF se usa directamente."""
    tf = ft.TextField(
        hint_text=hint,
        width=width,
        height=40,
        text_size=13,
        border_color=C_BORDER,
        focused_border_color=C_ACCENT,
        bgcolor=C_SURFACE2,
        color=C_TEXT,
        cursor_color=C_ACCENT,
        content_padding=ft.Padding(left=10, top=6, right=10, bottom=6),
    )
    return ft.Column(
        [ft.Text(label, size=11, color=C_MUTED, weight=ft.FontWeight.W_500), tf],
        spacing=4,
    ), tf


def mk_btn(texto, on_click, icon=None, color=C_ACCENT):
    partes = []
    if icon:
        partes.append(ft.Icon(icon, size=15, color="#ffffff"))
    partes.append(ft.Text(texto, color="#ffffff", size=13))
    return ft.Button(
        content=ft.Row(partes, spacing=6, tight=True),
        on_click=on_click,
        style=ft.ButtonStyle(
            bgcolor={"": color, "hovered": C_ACCENT2},
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.Padding(left=16, top=10, right=16, bottom=10),
        ),
    )


def mk_btn2(texto, on_click, icon=None):
    partes = []
    if icon:
        partes.append(ft.Icon(icon, size=15, color=C_TEXT))
    partes.append(ft.Text(texto, color=C_TEXT, size=13))
    return ft.Button(
        content=ft.Row(partes, spacing=6, tight=True),
        on_click=on_click,
        style=ft.ButtonStyle(
            bgcolor={"": "transparent"},
            side={"": ft.BorderSide(1, C_BORDER)},
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.Padding(left=14, top=10, right=14, bottom=10),
        ),
    )


def mk_section(titulo, contenido):
    return ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Container(width=3, height=18, bgcolor=C_ACCENT, border_radius=2),
                ft.Text(titulo, size=13, weight=ft.FontWeight.W_600, color=C_TEXT),
            ], spacing=8),
            ft.Container(height=1, bgcolor=C_BORDER),
            contenido,
        ], spacing=10),
        bgcolor=C_SURFACE, border_radius=8, padding=16,
        border=ft.Border.all(1, C_BORDER),
    )


def mk_table(columnas):
    """Devuelve (Container-scroll, DataTable). Sin ft.Ref — objetos directos."""
    dt = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text(c, size=11, weight=ft.FontWeight.W_600, color=C_MUTED))
            for c in columnas
        ],
        rows=[],
        border=ft.Border.all(1, C_BORDER),
        border_radius=6,
        heading_row_color=C_SURFACE2,
        heading_row_height=36,
        data_row_min_height=32,
        data_row_max_height=40,
        horizontal_lines=ft.BorderSide(1, C_BORDER),
        column_spacing=16,
    )
    ct = ft.Container(
        content=ft.Column(
            [ft.Row([dt], scroll=ft.ScrollMode.AUTO)],
            scroll=ft.ScrollMode.AUTO,
        ),
        bgcolor=C_SURFACE, border_radius=8, padding=8, expand=True,
    )
    return ct, dt


_TAG_COLORS = {
    "ok":    ft.Colors.with_opacity(0.06, C_OK),
    "aviso": ft.Colors.with_opacity(0.10, C_WARN),
    "error": ft.Colors.with_opacity(0.10, C_ERR),
    "info":  ft.Colors.with_opacity(0.08, C_INFO),
}


def fill_table(dt, rows, tag_fn=None):
    """Rellena un DataTable directamente (sin Ref)."""
    dt.rows.clear()
    for row in rows:
        tag   = tag_fn(row) if tag_fn else None
        color = _TAG_COLORS.get(tag)
        dt.rows.append(ft.DataRow(
            cells=[
                ft.DataCell(ft.Text(
                    str(v) if v is not None else "—",
                    size=12, color=C_TEXT,
                ))
                for v in row
            ],
            color=color,
        ))


def sep_label(txt):
    return ft.Text(txt, size=11, color=C_MUTED, weight=ft.FontWeight.W_500)


def mk_kpi_card(titulo, valor_control, subtitulo, icono, color=C_ACCENT):
    """Tarjeta pequeña para el resumen inicial."""
    return ft.Container(
        content=ft.Row([
            ft.Container(
                content=ft.Icon(icono, color=color, size=22),
                width=44, height=44, alignment=ft.Alignment(0, 0),
                bgcolor=ft.Colors.with_opacity(0.10, color),
                border_radius=12,
            ),
            ft.Column([
                ft.Text(titulo, size=11, color=C_MUTED, weight=ft.FontWeight.W_600),
                valor_control,
                ft.Text(subtitulo, size=10, color=C_MUTED),
            ], spacing=1, expand=True),
        ], spacing=12),
        bgcolor=C_SURFACE, border_radius=14, padding=16,
        border=ft.Border.all(1, C_BORDER),
        width=250,
    )


def aplicar_tema_visual(page, modo_claro=False):
    """Aplica tema claro/oscuro a los controles ya creados.

    No reconstruye la app: recorre los controles visibles, actualiza colores base
    y deja que los refrescos de tablas regeneren filas con la paleta nueva.
    """
    modo = "light" if modo_claro else "dark"
    old_values = {v for pal in THEMES.values() for v in pal.values()}
    old_bg = {THEMES["dark"]["bg"], THEMES["light"]["bg"]}
    old_surface = {THEMES["dark"]["surface"], THEMES["light"]["surface"]}
    old_surface2 = {THEMES["dark"]["surface2"], THEMES["light"]["surface2"]}
    old_border = {THEMES["dark"]["border"], THEMES["light"]["border"]}
    old_text = {THEMES["dark"]["text"], THEMES["light"]["text"]}
    old_muted = {THEMES["dark"]["muted"], THEMES["light"]["muted"]}

    set_palette(modo)
    page.theme_mode = ft.ThemeMode.LIGHT if modo_claro else ft.ThemeMode.DARK
    page.bgcolor = C_BG

    def recolor(ctrl):

        if isinstance(ctrl, ft.Text):
            if ctrl.color in old_muted:
                ctrl.color = C_MUTED
            elif ctrl.color in old_text or ctrl.color in old_values:
                ctrl.color = C_TEXT

        if isinstance(ctrl, ft.TextField):
            ctrl.bgcolor = C_SURFACE2
            ctrl.border_color = C_BORDER
            ctrl.focused_border_color = C_ACCENT
            ctrl.color = C_TEXT
            ctrl.cursor_color = C_ACCENT

        if isinstance(ctrl, ft.Container):
            if ctrl.bgcolor in old_bg:
                ctrl.bgcolor = C_BG
            elif ctrl.bgcolor in old_surface:
                ctrl.bgcolor = C_SURFACE
            elif ctrl.bgcolor in old_surface2:
                ctrl.bgcolor = C_SURFACE2
            if getattr(ctrl, "border", None):
                ctrl.border = ft.Border.all(1, C_BORDER)

        if isinstance(ctrl, ft.DataTable):
            ctrl.border = ft.Border.all(1, C_BORDER)
            ctrl.heading_row_color = C_SURFACE2
            ctrl.horizontal_lines = ft.BorderSide(1, C_BORDER)
            for col in ctrl.columns:
                if isinstance(col.label, ft.Text):
                    col.label.color = C_MUTED
            for row in ctrl.rows:
                for cell in row.cells:
                    if isinstance(cell.content, ft.Text):
                        cell.content.color = C_TEXT

        if isinstance(ctrl, ft.TabBar):
            ctrl.indicator_color = C_ACCENT
            ctrl.label_color = C_TEXT
            ctrl.unselected_label_color = C_MUTED
            ctrl.divider_color = C_BORDER

        for attr in ("content", "controls", "tabs"):
            child = getattr(ctrl, attr, None)
            if isinstance(child, list):
                for c in child:
                    recolor(c)
            elif child is not None:
                recolor(child)

    for root in page.controls:
        recolor(root)
    page.update()


def main(page: ft.Page):
    page.title       = "Pharma Control"
    page.theme_mode  = ft.ThemeMode.DARK
    page.bgcolor     = C_BG
    page.window.width      = 1360
    page.window.height     = 860
    page.window.min_width  = 1100
    page.window.min_height = 700
    page.padding = 0
    page.fonts   = {"mono": "Courier New"}

    arduino      = None
    continuar    = threading.Event()
    continuar.set()
    serial_queue = queue.Queue()

    lbl_status  = ft.Text("Iniciando...", size=11, color=C_MUTED)
    lbl_cfg_act = ft.Text("Sin espacio activo", size=11, color=C_WARN)

    lbl_alarma   = ft.Text("✓ ESTADO NORMAL", size=13,
                            weight=ft.FontWeight.W_700, color=C_OK)
    badge_alarma = ft.Container(
        content=lbl_alarma,
        bgcolor=ft.Colors.with_opacity(0.10, C_OK),
        border_radius=6,
        padding=ft.Padding(left=16, top=8, right=16, bottom=8),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.3, C_OK)),
    )

    terminal_list = ft.ListView(
        expand=True,
        spacing=0,
        auto_scroll=True,
        padding=ft.Padding(left=12, top=8, right=12, bottom=8),
    )

    def terminal_add(line: str):
        """Añade una línea cruda al ListView del terminal."""
        terminal_list.controls.append(
            ft.Text(
                line,
                size=12,
                color=C_OK,
                font_family="mono",
                selectable=True,
            )
        )
        if len(terminal_list.controls) > 250:
            terminal_list.controls.pop(0)

    def terminal_clear():
        terminal_list.controls.clear()

    kpi_meds = ft.Text("—", size=25, weight=ft.FontWeight.W_700, color=C_TEXT)
    kpi_stock = ft.Text("—", size=25, weight=ft.FontWeight.W_700, color=C_TEXT)
    kpi_cad = ft.Text("—", size=25, weight=ft.FontWeight.W_700, color=C_TEXT)
    kpi_inc = ft.Text("—", size=25, weight=ft.FontWeight.W_700, color=C_TEXT)
    kpi_alertas = ft.Text("—", size=25, weight=ft.FontWeight.W_700, color=C_TEXT)

    ct_meds,  dt_meds  = mk_table([
        "ID med", "Espacio", "T min", "T max", "H min", "H max",
        "Gracia (min)", "Stock", "Stock mín", "Caducidad", "Días", "Caducado", "Aviso",
    ])
    c_mdel,   f_mdel   = mk_field("ID a eliminar *",    "Ej: 1001", width=180)
    
    ct_stock, dt_stock = mk_table([
        "ID med", "Cantidad", "Stock mín", "Última actualización",
    ])
    ct_cad,   dt_cad   = mk_table([
        "ID med", "Fecha caducidad", "Días restantes", "Caducado", "Aviso", "Motivo",
    ])
    ct_cfg,   dt_cfg   = mk_table([
        "ID", "Nombre",
        "T min esp", "T max esp", "H min esp", "H max esp",
        "T min ctrl", "T max ctrl", "H min ctrl", "H max ctrl",
        "Seleccionada",
    ])
    ct_hamb,  dt_hamb  = mk_table([
        "ID", "Espacio",
        "T min ctrl", "T max ctrl", "T min hist", "T max hist",
        "H min ctrl", "H max ctrl", "H min hist", "H max hist",
    ])
    ct_iesp,  dt_iesp  = mk_table([
        "ID", "ID esp", "Inicio", "Fin", "Dur. min",
        "T inicio", "H inicio", "T fin", "H fin", "Motivo", "Estado",
    ])
    ct_mamb,  dt_mamb  = mk_table([
        "ID medición", "ID esp", "Espacio", "Temp (°C)", "Hum (%)", "Fecha",
    ])
    ct_imed,  dt_imed  = mk_table([
        "ID", "ID med", "ID esp", "Inicio", "Fin", "Dur (min)",
        "T inicio", "H inicio", "Aviso", "Caducado", "Motivo", "Estado",
    ])
    ct_alert, dt_alert = mk_table([
        "Origen", "Tipo", "ID Med", "ID Espacio", "Mensaje", "Fecha",
    ])
    ct_hlogs, dt_hlogs = mk_table([
        "ID log", "ID med", "ID esp", "T min", "T max", "H min", "H max",
        "Caducidad", "1ª inserción", "Última salida", "Cant.", "Estado", "Obs.",
    ])
    ct_hmovs, dt_hmovs = mk_table([
        "ID mov", "ID med", "Tipo", "Cantidad", "Fecha", "Observaciones",
    ])
    ct_hmedc, dt_hmedc = mk_table([
        "ID medición", "ID espacio", "Temperatura", "Humedad", "Fecha",
    ])
    ct_hregs, dt_hregs = mk_table([
        "ID reg", "ID med", "ID esp",
        "Caducidad", "Cantidad",
        "T min med", "T max med", "H min med", "H max med",
        "Gracia (min)", "Fecha registro",
    ])
    ct_dash_meds, dt_dash_meds = mk_table([
        "ID med", "Espacio", "Stock", "Caducidad", "Días", "Estado",
    ])
    ct_dash_alert, dt_dash_alert = mk_table([
        "Tipo", "ID med", "ID esp", "Mensaje", "Fecha",
    ])
    ct_dash_med, dt_dash_med = mk_table([
        "Espacio", "Temp", "Hum", "Fecha",
    ])

    c_mid,    f_mid    = mk_field("ID medicamento *",   "Ej: 1001", width=180)
    c_mcfg,   f_mcfg   = mk_field("ID espacio (opc.)",  "vacío = activo", width=180)
    c_mfecha, f_mfecha = mk_field("Fecha caducidad *",  "YYYY-MM-DD", width=180)
    c_mcant,  f_mcant  = mk_field("Cantidad *",         "Ej: 50", width=130)
    c_mtmin,  f_mtmin  = mk_field("T min med *",        "°C", width=130)
    c_mtmax,  f_mtmax  = mk_field("T max med *",        "°C", width=130)
    c_mhmin,  f_mhmin  = mk_field("H min med *",        "%", width=130)
    c_mhmax,  f_mhmax  = mk_field("H max med *",        "%", width=130)
    c_mgrac,  f_mgrac  = mk_field("Gracia (min)",       "60", width=120)
    f_mgrac.value = "60"

    c_sid,    f_sid    = mk_field("ID medicamento *",  "Ej: 1001", width=180)
    c_scant,  f_scant  = mk_field("Cantidad *",        "Ej: 20", width=130)
    c_sfecha, f_sfecha = mk_field("Nueva caducidad *", "YYYY-MM-DD", width=200)

    c_cid,    f_cid    = mk_field("ID medicamento *", "Ej: 1001", width=180)
    c_cfecha, f_cfecha = mk_field("Nueva fecha *",    "YYYY-MM-DD", width=200)

    c_cnombre, f_cnombre = mk_field("Nombre espacio *", "", width=280)
    c_ctmin,   f_ctmin   = mk_field("T min esp *",      "°C", width=130)
    c_ctmax,   f_ctmax   = mk_field("T max esp *",      "°C", width=130)
    c_chmin,   f_chmin   = mk_field("H min esp *",      "%", width=130)
    c_chmax,   f_chmax   = mk_field("H max esp *",      "%", width=130)
    c_cselid,  f_cselid  = mk_field("ID espacio",       "Ej: 1", width=150)

    def gv(tf):
        """Obtiene el valor de un TextField como string limpio."""
        return (tf.value or "").strip()

    def gv_float(tf):
        """Devuelve float o None si vacío."""
        v = gv(tf)
        return float(v) if v else None

    def gv_int(tf, default=None):
        """Devuelve int o default si vacío."""
        v = gv(tf)
        return int(v) if v else default

    def limpiar(*tfs):
        for tf in tfs:
            tf.value = ""
        page.update()

    def snack(msg, color=C_OK):
        """Muestra un aviso inferior. Flet 0.85 usa page.show_dialog()."""
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(msg, color="#ffffff"),
                bgcolor=ft.Colors.with_opacity(0.92, color),
                duration=4000,
            )
        )
        page.update()

    def safe(fn):
        """Ejecuta fn capturando errores y mostrándolos en la status bar."""
        try:
            fn()
        except Exception as ex:
            lbl_status.value = f"Error: {texto_error_mysql(ex)}"
            page.update()

    def actualizar_label_cfg():
        """Actualiza el indicador del espacio activo en la barra superior."""
        cfg = get_config_activa()
        if cfg:
            lbl_cfg_act.value = f"Espacio activo: [{cfg[0]}] {cfg[1]}"
            lbl_cfg_act.color = C_OK
        else:
            lbl_cfg_act.value = "Sin espacio activo"
            lbl_cfg_act.color = C_WARN
        page.update()

    def cambiar_tema(e):
        aplicar_tema_visual(page, modo_claro=bool(e.control.value))
        refrescar_todo()

    sw_theme = ft.Switch(
        label="Modo claro",
        value=False,
        on_change=cambiar_tema,
    )

    def guardar_med(e):
        try:
            id_med = gv_int(f_mid)
            if not id_med:
                raise ValueError("El ID del medicamento es obligatorio")

            id_cfg  = gv_int(f_mcfg)
            fecha   = gv(f_mfecha)
            if not fecha:
                raise ValueError("La fecha de caducidad es obligatoria")
            datetime.strptime(fecha, "%Y-%m-%d")

            cant   = gv_int(f_mcant)
            if not cant or cant <= 0:
                raise ValueError("La cantidad debe ser mayor que 0")

            tmin   = gv_float(f_mtmin)
            tmax   = gv_float(f_mtmax)
            hmin   = gv_float(f_mhmin)
            hmax   = gv_float(f_mhmax)

            if None in (tmin, tmax, hmin, hmax):
                raise ValueError("Los rangos de temperatura y humedad son obligatorios")
            if tmin >= tmax:
                raise ValueError("T min debe ser menor que T max")
            if hmin >= hmax:
                raise ValueError("H min debe ser menor que H max")

            gracia = gv_int(f_mgrac, default=60)

            t_amb = None
            h_amb = None

            ejecutar_query(
                "INSERT INTO registro_entrada "
                "(id_med, id_config, temperatura_ambiente, humedad_ambiente, "
                "fecha_caducidad, cantidad, "
                "temp_min_med, temp_max_med, hum_min_med, hum_max_med, "
                "tiempo_gracia_minutos) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (id_med, id_cfg, t_amb, h_amb,
                 fecha, cant,
                 tmin, tmax, hmin, hmax,
                 gracia),
            )

            rechazo = ejecutar_query(
                "SELECT tipo_alerta, mensaje FROM alertas "
                "WHERE id_med = %s "
                "AND tipo_alerta IN ("
                "  'MEDICAMENTO NO PERMITIDO','CANTIDAD INVALIDA',"
                "  'ERROR CONFIGURACION','ERROR ESPACIO'"
                ") ORDER BY fecha DESC LIMIT 1",
                (id_med,), fetch=True,
            )
            ts = datetime.now().strftime("%H:%M:%S")
            if rechazo:
                terminal_add(f"[{ts}] ⚠ MED {id_med} — rechazado por BD: {rechazo[0][1]}")
                snack(f"⚠ Entrada registrada pero rechazada por MySQL:\n{rechazo[0][1]}", C_WARN)
            else:
                terminal_add(f"[{ts}] ✓ MED {id_med} registrado — cant: {cant}  cad: {fecha}  T:[{tmin},{tmax}]  H:[{hmin},{hmax}]")
                snack("✓ Medicamento registrado correctamente")

            limpiar(f_mid, f_mcfg, f_mfecha, f_mcant,
                    f_mtmin, f_mtmax, f_mhmin, f_mhmax)
            f_mgrac.value = "60"
            refrescar_todo()

        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ MED — error al registrar: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)

    def eliminar_med(e):
        try:
            id_med = gv_int(f_mdel)
            if not id_med:
                raise ValueError("El ID del medicamento es obligatorio")

            existe = ejecutar_query(
                "SELECT COUNT(*) FROM medicamentos WHERE id_med = %s",
                (id_med,), fetch=True,
            )
            if not existe or existe[0][0] == 0:
                raise ValueError(f"No existe ningún medicamento con ID {id_med}")

            ejecutar_query(
                "DELETE FROM medicamentos WHERE id_med = %s",
                (id_med,),
            )

            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] 🗑 MED {id_med} eliminado — caducidades y stock borrados en cascade")
            snack(f"✓ Medicamento {id_med} eliminado correctamente")
            limpiar(f_mdel)
            refrescar_todo()

        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ MED — error al eliminar: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)


    def cargar_meds():
        rows = ejecutar_query(
            "SELECT m.id_med, ce.nombre_espacio, "
            "m.temp_min, m.temp_max, m.hum_min, m.hum_max, "
            "m.tiempo_gracia_minutos, "
            "s.cantidad, s.stock_min, "
            "c.fecha_caducidad, c.dias_restantes, "
            "IF(c.caducado,'Sí','No'), IF(c.aviso_caducidad,'Sí','No') "
            "FROM medicamentos m "
            "LEFT JOIN configuracion_espacio ce ON m.id_config = ce.id_config "
            "LEFT JOIN stock s ON m.id_med = s.id_med "
            "LEFT JOIN caducidades c ON m.id_med = c.id_med "
            "ORDER BY m.id_med",
            fetch=True,
        )
        fill_table(dt_meds, rows,
                   lambda r: "error" if r[11] == "Sí"
                             else ("aviso" if r[12] == "Sí" else "ok"))
        page.update()

    def reponer(e):
        try:
            id_med = gv_int(f_sid)
            if not id_med:
                raise ValueError("ID medicamento obligatorio")
            cant   = gv_int(f_scant)
            if not cant or cant <= 0:
                raise ValueError("Cantidad debe ser > 0")
            fecha  = gv(f_sfecha)
            if not fecha:
                raise ValueError("Fecha de caducidad obligatoria para reposición")
            datetime.strptime(fecha, "%Y-%m-%d")

            ejecutar_procedure("reponer_stock", (id_med, cant, fecha))
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✓ STOCK — reposición MED {id_med}: +{cant} uds  nueva cad: {fecha}")
            snack("✓ Stock repuesto correctamente")
            limpiar(f_sid, f_scant, f_sfecha)
            refrescar_todo()
        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ STOCK — error reposición MED {id_med}: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)

    def retirar(e):
        try:
            id_med = gv_int(f_sid)
            if not id_med:
                raise ValueError("ID medicamento obligatorio")
            cant   = gv_int(f_scant)
            if not cant or cant <= 0:
                raise ValueError("Cantidad debe ser > 0")

            ejecutar_procedure("retirar_stock", (id_med, cant))
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✓ STOCK — retirada MED {id_med}: -{cant} uds")
            snack("✓ Stock retirado correctamente")
            limpiar(f_sid, f_scant, f_sfecha)
            refrescar_todo()
        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ STOCK — error retirada MED {id_med}: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)

    def cargar_stock():
        rows = ejecutar_query(
            "SELECT id_med, cantidad, stock_min, ultima_actualizacion "
            "FROM stock ORDER BY id_med",
            fetch=True,
        )
        fill_table(dt_stock, rows,
                   lambda r: "aviso" if (r[1] or 0) <= (r[2] or 0) else "ok")
        page.update()

    def act_caducidad(e):
        try:
            id_med = gv_int(f_cid)
            if not id_med:
                raise ValueError("ID medicamento obligatorio")
            fecha  = gv(f_cfecha)
            if not fecha:
                raise ValueError("Fecha obligatoria")
            datetime.strptime(fecha, "%Y-%m-%d")

            ejecutar_procedure("actualizar_caducidad", (id_med, fecha))
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✓ CAD — MED {id_med}: nueva caducidad {fecha}")
            snack("✓ Fecha de caducidad actualizada")
            limpiar(f_cid, f_cfecha)
            refrescar_todo()
        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ CAD — error MED {id_med}: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)

    def cargar_cad():
        rows = ejecutar_query(
            "SELECT id_med, fecha_caducidad, dias_restantes, "
            "IF(caducado,'Sí','No'), IF(aviso_caducidad,'Sí','No'), motivo_caducidad "
            "FROM caducidades ORDER BY dias_restantes ASC",
            fetch=True,
        )
        fill_table(dt_cad, rows,
                   lambda r: "error" if r[3] == "Sí"
                             else ("aviso" if r[4] == "Sí" else "ok"))
        page.update()

    def guardar_cfg(seleccionar=False):
        try:
            nombre = gv(f_cnombre)
            if not nombre:
                raise ValueError("El nombre del espacio es obligatorio")
            tmin = gv_float(f_ctmin)
            tmax = gv_float(f_ctmax)
            hmin = gv_float(f_chmin)
            hmax = gv_float(f_chmax)
            if None in (tmin, tmax, hmin, hmax):
                raise ValueError("Todos los rangos son obligatorios")
            if tmin >= tmax:
                raise ValueError("T min debe ser < T max")
            if hmin >= hmax:
                raise ValueError("H min debe ser < H max")

            conn = conectar_mysql()
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO configuracion_espacio "
                    "(nombre_espacio, "
                    "temp_min_espacio, temp_max_espacio, "
                    "hum_min_espacio,  hum_max_espacio, "
                    "temp_min_control, temp_max_control, "
                    "hum_min_control,  hum_max_control, "
                    "seleccionada) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE)",
                    (nombre, tmin, tmax, hmin, hmax,
                     tmin, tmax, hmin, hmax),
                )
                id_cfg = cur.lastrowid
                conn.commit()
            finally:
                if conn and conn.is_connected():
                    conn.close()

            if seleccionar:
                ejecutar_procedure("seleccionar_configuracion", (id_cfg,))
                ts = datetime.now().strftime("%H:%M:%S")
                terminal_add(f"[{ts}] ✓ ESP — creado y activado '{nombre}' (ID {id_cfg})  T:[{tmin},{tmax}]  H:[{hmin},{hmax}]")
                snack(f"✓ Espacio guardado y seleccionado (ID {id_cfg})")
            else:
                ts = datetime.now().strftime("%H:%M:%S")
                terminal_add(f"[{ts}] ✓ ESP — creado '{nombre}' (ID {id_cfg})  T:[{tmin},{tmax}]  H:[{hmin},{hmax}]")
                snack(f"✓ Espacio guardado (ID {id_cfg})")

            limpiar(f_cnombre, f_ctmin, f_ctmax, f_chmin, f_chmax)
            cargar_cfg_tabla()
            actualizar_label_cfg()
            enviar_rangos_arduino()

        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ ESP — error al guardar espacio: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)

    def sel_cfg_manual(e):
        try:
            id_cfg = gv_int(f_cselid)
            if not id_cfg:
                raise ValueError("Introduce un ID de espacio válido")
            ejecutar_procedure("seleccionar_configuracion", (id_cfg,))
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✓ ESP — espacio ID {id_cfg} activado como activo")
            snack(f"✓ Espacio {id_cfg} seleccionado como activo")
            limpiar(f_cselid)
            cargar_cfg_tabla()
            actualizar_label_cfg()
            enviar_rangos_arduino()
        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ ESP — error al activar espacio {id_cfg}: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)

    def cargar_cfg_tabla():
        rows = ejecutar_query(
            "SELECT id_config, nombre_espacio, "
            "temp_min_espacio, temp_max_espacio, hum_min_espacio, hum_max_espacio, "
            "temp_min_control, temp_max_control, hum_min_control, hum_max_control, "
            "IF(seleccionada,'Sí','No') "
            "FROM configuracion_espacio ORDER BY id_config",
            fetch=True,
        )
        fill_table(dt_cfg, rows, lambda r: "info" if r[10] == "Sí" else None)
        page.update()

    def cargar_ambiente():
        rows = ejecutar_query(
            "SELECT ce.id_config, ce.nombre_espacio, "
            "ce.temp_min_control, ce.temp_max_control, "
            "he.temp_min_hist, he.temp_max_hist, "
            "ce.hum_min_control, ce.hum_max_control, "
            "he.hum_min_hist, he.hum_max_hist "
            "FROM configuracion_espacio ce "
            "LEFT JOIN historial_espacio he ON ce.id_config = he.id_config "
            "ORDER BY ce.id_config",
            fetch=True,
        )
        fill_table(dt_hamb, rows)

        inc = ejecutar_query(
            "SELECT id_incidencia, id_config, fecha_inicio, fecha_fin, "
            "duracion_minutos, temperatura_inicio, humedad_inicio, "
            "temperatura_fin, humedad_fin, motivo, estado "
            "FROM incidencias_espacio ORDER BY fecha_inicio DESC LIMIT 200",
            fetch=True,
        )
        fill_table(dt_iesp, inc, lambda r: "error" if r[10] == "ABIERTA" else "ok")

        med = ejecutar_query(
            "SELECT me.id_medicion, me.id_config, ce.nombre_espacio, "
            "me.temperatura, me.humedad, me.fecha_medicion "
            "FROM mediciones_espacio me "
            "LEFT JOIN configuracion_espacio ce ON me.id_config = ce.id_config "
            "ORDER BY me.fecha_medicion DESC LIMIT 200",
            fetch=True,
        )
        fill_table(dt_mamb, med)

        im = ejecutar_query(
            "SELECT id_incidencia_med, id_med, id_config, "
            "fecha_inicio, fecha_fin, duracion_minutos, "
            "temperatura_inicio, humedad_inicio, "
            "IF(aviso_emitido,'Sí','No'), "
            "IF(caducado_por_incidencia,'Sí','No'), "
            "motivo, estado "
            "FROM incidencias_medicamento "
            "ORDER BY fecha_inicio DESC LIMIT 300",
            fetch=True,
        )
        fill_table(dt_imed, im,
                   lambda r: "error" if r[11] == "ABIERTA" or r[9] == "Sí"
                             else ("aviso" if r[8] == "Sí" else "ok"))
        page.update()

    def cargar_alertas():
        alertas = ejecutar_query(
            "SELECT 'ALERTA', tipo_alerta, id_med, id_config, mensaje, fecha "
            "FROM alertas ORDER BY fecha DESC",
            fetch=True,
        )
        inc_esp = ejecutar_query(
            "SELECT 'INCIDENCIA ESPACIO', estado, NULL, id_config, motivo, fecha_inicio "
            "FROM incidencias_espacio WHERE estado = 'ABIERTA' "
            "ORDER BY fecha_inicio DESC",
            fetch=True,
        )
        inc_med = ejecutar_query(
            "SELECT 'INCIDENCIA MEDICAMENTO', estado, id_med, id_config, motivo, fecha_inicio "
            "FROM incidencias_medicamento WHERE estado = 'ABIERTA' "
            "ORDER BY fecha_inicio DESC",
            fetch=True,
        )

        total = list(inc_esp) + list(inc_med) + list(alertas)

        def tag_a(r):
            origen, tipo = r[0], r[1]
            if origen in ("INCIDENCIA ESPACIO", "INCIDENCIA MEDICAMENTO"):
                return "error"
            if tipo in ("CADUCADO", "SIN STOCK", "MEDICAMENTO NO PERMITIDO",
                        "CADUCADO POR AMBIENTE", "ESPACIO FUERA DE RANGO",
                        "CANTIDAD INVALIDA", "ERROR CONFIGURACION", "ERROR ESPACIO"):
                return "error"
            if tipo in ("AVISO CADUCIDAD", "STOCK BAJO", "AVISO FUERA DE RANGO"):
                return "aviso"
            return "info"

        fill_table(dt_alert, total, tag_a)

        hay_inc   = bool(inc_esp or inc_med)
        hay_alert = bool(alertas)

        if hay_inc:
            lbl_alarma.value     = "⚠  ALERTA AMBIENTAL CRÍTICA"
            lbl_alarma.color     = C_ERR
            badge_alarma.bgcolor = ft.Colors.with_opacity(0.12, C_ERR)
            badge_alarma.border  = ft.Border.all(1, ft.Colors.with_opacity(0.4, C_ERR))
        elif hay_alert:
            lbl_alarma.value     = "AVISO ACTIVO"
            lbl_alarma.color     = C_WARN
            badge_alarma.bgcolor = ft.Colors.with_opacity(0.12, C_WARN)
            badge_alarma.border  = ft.Border.all(1, ft.Colors.with_opacity(0.4, C_WARN))
        else:
            lbl_alarma.value     = "✓  ESTADO NORMAL"
            lbl_alarma.color     = C_OK
            badge_alarma.bgcolor = ft.Colors.with_opacity(0.10, C_OK)
            badge_alarma.border  = ft.Border.all(1, ft.Colors.with_opacity(0.3, C_OK))

        page.update()

    def cargar_historico():
        rows = ejecutar_query(
            "SELECT id_log, id_med, id_config, "
            "temp_min, temp_max, hum_min, hum_max, "
            "fecha_caducidad, fecha_primera_insercion, fecha_ultima_salida, "
            "cantidad_inicial, estado_actual, observaciones "
            "FROM historico_logs ORDER BY id_log DESC LIMIT 500",
            fetch=True,
        )
        fill_table(dt_hlogs, rows,
                   lambda r: "error" if r[11] == "CADUCADO"
                             else ("aviso" if r[11] == "SIN STOCK" else "ok"))

        movs = ejecutar_query(
            "SELECT id_mov, id_med, tipo_mov, cantidad, fecha, observaciones "
            "FROM movimientos_stock ORDER BY fecha DESC LIMIT 500",
            fetch=True,
        )
        fill_table(dt_hmovs, movs,
                   lambda r: "ok" if r[2] == "ENTRADA" else "aviso")

        meds = ejecutar_query(
            "SELECT id_medicion, id_config, temperatura, humedad, fecha_medicion "
            "FROM mediciones_espacio ORDER BY fecha_medicion DESC LIMIT 500",
            fetch=True,
        )
        fill_table(dt_hmedc, meds)

        regs = ejecutar_query(
            "SELECT id_registro, id_med, id_config, "
            "fecha_caducidad, cantidad, "
            "temp_min_med, temp_max_med, hum_min_med, hum_max_med, "
            "tiempo_gracia_minutos, fecha_registro "
            "FROM registro_entrada ORDER BY fecha_registro DESC LIMIT 500",
            fetch=True,
        )
        fill_table(dt_hregs, regs)
        page.update()

    def cargar_dashboard():
        """Carga los indicadores y las tablas esenciales del resumen."""
        def scalar(query):
            r = ejecutar_query(query, fetch=True)
            return r[0][0] if r else 0

        kpi_meds.value = str(scalar("SELECT COUNT(*) FROM medicamentos"))
        kpi_stock.value = str(scalar("SELECT COUNT(*) FROM stock WHERE cantidad <= stock_min"))
        kpi_cad.value = str(scalar("SELECT COUNT(*) FROM caducidades WHERE aviso_caducidad = TRUE AND caducado = FALSE"))
        kpi_alertas.value = str(scalar("SELECT COUNT(*) FROM alertas"))
        kpi_inc.value = str(scalar(
            "SELECT "
            "(SELECT COUNT(*) FROM incidencias_espacio WHERE estado='ABIERTA') + "
            "(SELECT COUNT(*) FROM incidencias_medicamento WHERE estado='ABIERTA')"
        ))

        meds = ejecutar_query(
            "SELECT m.id_med, COALESCE(ce.nombre_espacio, '—'), "
            "COALESCE(s.cantidad, 0), c.fecha_caducidad, c.dias_restantes, "
            "CASE "
            " WHEN c.caducado THEN 'Caducado' "
            " WHEN c.aviso_caducidad THEN 'Aviso' "
            " WHEN COALESCE(s.cantidad, 0) <= COALESCE(s.stock_min, 0) THEN 'Stock bajo' "
            " ELSE 'OK' END "
            "FROM medicamentos m "
            "LEFT JOIN configuracion_espacio ce ON m.id_config = ce.id_config "
            "LEFT JOIN stock s ON m.id_med = s.id_med "
            "LEFT JOIN caducidades c ON m.id_med = c.id_med "
            "ORDER BY c.dias_restantes ASC, m.id_med LIMIT 12",
            fetch=True,
        )
        fill_table(dt_dash_meds, meds,
                   lambda r: "error" if r[5] == "Caducado" else ("aviso" if r[5] in ("Aviso", "Stock bajo") else "ok"))

        al = ejecutar_query(
            "SELECT tipo_alerta, id_med, id_config, mensaje, fecha "
            "FROM alertas ORDER BY fecha DESC LIMIT 10",
            fetch=True,
        )
        fill_table(dt_dash_alert, al, lambda r: "error" if r[0] in ("CADUCADO", "SIN STOCK", "CADUCADO POR AMBIENTE", "ESPACIO FUERA DE RANGO") else "aviso")

        med = ejecutar_query(
            "SELECT COALESCE(ce.nombre_espacio, me.id_config), me.temperatura, me.humedad, me.fecha_medicion "
            "FROM mediciones_espacio me "
            "LEFT JOIN configuracion_espacio ce ON me.id_config = ce.id_config "
            "ORDER BY me.fecha_medicion DESC LIMIT 10",
            fetch=True,
        )
        fill_table(dt_dash_med, med)
        page.update()

    def enviar_arduino(cmd):
        nonlocal arduino
        if arduino and arduino.is_open:
            try:
                arduino.write((cmd + "\n").encode())
            except Exception:
                pass

    _ultimo_rango_enviado = [None]

    def enviar_rangos_arduino():
        """
        Envía los rangos de control del espacio activo al Arduino en formato:
          tMin,tMax,hMin,hMax\n
        El Arduino los lee con parseFloat() x4 para gestionar su propia alarma.
        Solo se envía si hay conexión activa y espacio seleccionado.
        """
        if not (arduino and arduino.is_open):
            return
        try:
            rows = ejecutar_query(
                "SELECT temp_min_control, temp_max_control, "
                "hum_min_control, hum_max_control "
                "FROM configuracion_espacio WHERE seleccionada = TRUE LIMIT 1",
                fetch=True,
            )
            if rows:
                t_min, t_max, h_min, h_max = rows[0]
                if None not in (t_min, t_max, h_min, h_max):
                    cmd = f"{t_min},{t_max},{h_min},{h_max}\n"
                    arduino.write(cmd.encode())
                    rango = (t_min, t_max, h_min, h_max)
                    if rango != _ultimo_rango_enviado[0]:
                        ts = datetime.now().strftime("%H:%M:%S")
                        terminal_add(f"[{ts}] >> ARDUINO — rangos enviados: T:[{t_min},{t_max}]  H:[{h_min},{h_max}]")
                        _ultimo_rango_enviado[0] = rango
        except Exception:
            pass

    def lectura_serial():
        nonlocal arduino
        while continuar.is_set():
            if arduino and arduino.is_open:
                try:
                    linea = arduino.readline().decode("utf-8", errors="ignore").strip()
                    if linea:
                        serial_queue.put(linea)
                except Exception as ex:
                    serial_queue.put(f"⚠ ERROR SERIAL: {ex}")
            time.sleep(0.05)

    def reconectar(e):
        nonlocal arduino
        try:
            if arduino and arduino.is_open:
                arduino.close()
            arduino = buscar_arduino()
            act_status()
            ts = datetime.now().strftime("%H:%M:%S")
            if arduino:
                terminal_add(f"[{ts}] ✓ SERIAL — Arduino reconectado en {arduino.port}")
                snack("✓ Arduino reconectado", C_OK)
            else:
                terminal_add(f"[{ts}] ⚠ SERIAL — Arduino no encontrado")
                snack("⚠ Arduino no encontrado", C_WARN)
            page.update()
        except Exception as ex:
            ts = datetime.now().strftime("%H:%M:%S")
            terminal_add(f"[{ts}] ✗ SERIAL — error al reconectar: {texto_error_mysql(ex)}")
            snack(f"✗ {texto_error_mysql(ex)}", C_ERR)

    def init_arduino():
        nonlocal arduino
        arduino = buscar_arduino()
        act_status()
        ts = datetime.now().strftime("%H:%M:%S")
        if arduino:
            terminal_add(f"[{ts}] ✓ SERIAL — Arduino detectado en {arduino.port} ({SERIAL_BAUD} baud)")
        else:
            terminal_add(f"[{ts}] ⚠ SERIAL — Arduino no detectado al arrancar")
        page.update()

    def act_status():
        lbl_status.value = (
            f"🔌 Arduino: {arduino.port}" if arduino and arduino.is_open
            else "⚪ Sin Arduino"
        )
        page.update()

    def refrescar_todo():
        """Refresca todas las tablas. Cada función atrapa sus propios errores."""
        for fn in (cargar_dashboard, cargar_meds, cargar_stock, cargar_cad,
                   cargar_cfg_tabla, cargar_ambiente,
                   cargar_alertas, cargar_historico):
            safe(fn)
        actualizar_label_cfg()

    async def periodic_update():
        await asyncio.sleep(1.5)

        mediciones_acumuladas = []
        last_save_time    = time.time()
        last_refresh_time = time.time()
        INTERVALO_GUARDADO = 50
        INTERVALO_REFRESH  = REFRESH_MS / 1000

        while True:
            lineas_pendientes = []
            try:
                while True:
                    lineas_pendientes.append(serial_queue.get_nowait())
            except queue.Empty:
                pass

            for linea in lineas_pendientes:
                terminal_add(linea)

                id_barcode = parsear_id_barcode(linea)
                if id_barcode:
                    ts_scan = datetime.now().strftime("%H:%M:%S")
                    tab_actual = nav_tab_index[0]
                    if tab_actual == 1:
                        f_mid.value = id_barcode
                        terminal_add(f"[{ts_scan}] SCAN → Medicamentos: ID {id_barcode}")
                    elif tab_actual == 2:
                        f_sid.value = id_barcode
                        terminal_add(f"[{ts_scan}] SCAN → Stock: ID {id_barcode}")
                    elif tab_actual == 3:
                        f_cid.value = id_barcode
                        terminal_add(f"[{ts_scan}] SCAN → Caducidad: ID {id_barcode}")
                    else:
                        f_mid.value = id_barcode
                        nav.selected_index = 1
                        nav_tab_index[0] = 1
                        terminal_add(f"[{ts_scan}] SCAN → Medicamentos (auto): ID {id_barcode}")
                    snack(f"ID escaneada: {id_barcode}", C_INFO)

                datos = parsear_sensor(linea)
                if datos:
                    mediciones_acumuladas.append(datos)

            if lineas_pendientes:
                page.update()

            ahora = time.time()
            if ahora - last_save_time >= INTERVALO_GUARDADO and mediciones_acumuladas:
                temp_prom = sum(m[0] for m in mediciones_acumuladas) / len(mediciones_acumuladas)
                hum_prom  = sum(m[1] for m in mediciones_acumuladas) / len(mediciones_acumuladas)
                ts = datetime.now().strftime("%H:%M:%S")
                terminal_add(f"[{ts}] >> Guardando promedio de {len(mediciones_acumuladas)} muestras: T={temp_prom:.2f}°C  H={hum_prom:.2f}%")
                try:
                    cfg = get_config_activa()
                    if cfg:
                        ejecutar_procedure("registrar_medicion_espacio", (cfg[0], temp_prom, hum_prom))
                        lbl_status.value = f"✓ Guardado en BD: T={temp_prom:.2f}°C H={hum_prom:.2f}%"
                        terminal_add(f"[{ts}] ✓ Medición guardada en BD — espacio: {cfg[1]}")
                        safe(cargar_ambiente)
                        safe(cargar_alertas)
                        safe(cargar_cad)
                    else:
                        lbl_status.value = "⚠ No hay espacio activo seleccionado"
                        terminal_add(f"[{ts}] ⚠ Sin espacio activo — medición no guardada")
                except Exception as ex:
                    err = texto_error_mysql(ex)
                    lbl_status.value = f"✗ Error BD: {err}"
                    terminal_add(f"[{ts}] ✗ Error al guardar en BD: {err}")
                mediciones_acumuladas = []
                last_save_time = ahora
                page.update()

            if ahora - last_refresh_time >= INTERVALO_REFRESH:
                refrescar_todo()
                enviar_rangos_arduino()
                last_refresh_time = ahora

            await asyncio.sleep(0.1)

    nota_med = ft.Container(
        content=ft.Text(
            " Si dejas 'ID espacio' vacío, MySQL usa el espacio activo. "
            "El campo 'ID medicamento' se rellena automáticamente al escanear un código de barras. "
            "Las mediciones ambientales llegan desde Arduino como DATA:temperatura,humedad y se guardan automáticamente.",
            size=11, color=C_MUTED, italic=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.05, C_INFO),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.2, C_INFO)),
        border_radius=6, padding=10,
    )

    tab_meds = ft.Column([
        mk_section("Registrar medicamento / entrada de stock", ft.Column([
            nota_med,
            ft.Row([c_mid, c_mcfg, c_mfecha, c_mcant], spacing=12, wrap=True),
            ft.Row([c_mtmin, c_mtmax, c_mhmin, c_mhmax, c_mgrac], spacing=12, wrap=True),
            ft.Container(
                content=ft.Text(":)",
                                size=10, color=C_MUTED, italic=True),
                padding=ft.Padding(left=0, top=22, right=0, bottom=0),
            ),
            ft.Row([
                mk_btn("Guardar entrada", guardar_med, ft.Icons.SAVE),
                mk_btn2("Limpiar campos", lambda e: (
                    limpiar(f_mid, f_mcfg, f_mfecha, f_mcant,
                            f_mtmin, f_mtmax, f_mhmin, f_mhmax),
                    setattr(f_mgrac, "value", "60"),
                    page.update(),
                )),
            ], spacing=10)
        ], spacing=10)),
        mk_section("Eliminar medicamento", ft.Column([
            ft.Text(
                "⚠  Elimina el medicamento y, en cascade, su stock y caducidad activos. "
                "El histórico (historico_logs) se conserva.",
                size=11, color=C_MUTED, italic=True,
            ),
            ft.Row([
                c_mdel,
                ft.Column([
                    ft.Text("", size=11),
                    mk_btn("Eliminar medicamento", eliminar_med,
                           ft.Icons.DELETE_FOREVER, C_ERR),
                ], spacing=4),
            ], spacing=12, wrap=True),
        ], spacing=10)),
        ct_meds,
    ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)

    nota_stock = ft.Container(
        content=ft.Text(
            "  'Reponer' llama a reponer_stock() que suma cantidad y actualiza caducidad. "
            "'Retirar' llama a retirar_stock(); si el stock llega a 0, el medicamento "
            "se archiva automáticamente en historico_logs.",
            size=11, color=C_MUTED, italic=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.05, C_INFO),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.2, C_INFO)),
        border_radius=6, padding=10,
    )

    tab_stock = ft.Column([
        mk_section("Operaciones de stock", ft.Column([
            nota_stock,
            ft.Row([c_sid, c_scant, c_sfecha], spacing=12, wrap=True),
            ft.Row([
                mk_btn("Reponer stock", reponer, ft.Icons.ADD_CIRCLE_OUTLINE),
                mk_btn("Retirar stock", retirar, ft.Icons.REMOVE_CIRCLE_OUTLINE, "#6b7280"),
            ], spacing=10),
        ], spacing=10)),
        ct_stock,
    ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)

    tab_cad = ft.Column([
        mk_section("Actualizar fecha de caducidad", ft.Column([
            ft.Text(
                "Funciona para medicamentos activos y para registros en historico_logs.",
                size=11, color=C_MUTED, italic=True,
            ),
            ft.Row([c_cid, c_cfecha,
                    ft.Column([ft.Text("", size=11),
                               mk_btn("Actualizar caducidad", act_caducidad, ft.Icons.UPDATE)
                               ], spacing=4),
                    ], spacing=12, wrap=True),
        ], spacing=10)),
        ct_cad,
    ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)

    tab_cfg = ft.Column([
        mk_section("Crear espacio de confinamiento", ft.Column([
            ft.Text(
                "Los rangos de control se recalculan automáticamente al añadir medicamentos.",
                size=11, color=C_MUTED, italic=True,
            ),
            ft.Row([c_cnombre, c_ctmin, c_ctmax, c_chmin, c_chmax], spacing=12, wrap=True),
            ft.Row([
                mk_btn("Guardar espacio", lambda e: guardar_cfg(False), ft.Icons.SAVE),
                mk_btn("Guardar y seleccionar", lambda e: guardar_cfg(True),
                        ft.Icons.CHECK_CIRCLE, C_OK),
            ], spacing=10),
        ], spacing=10)),
        mk_section("Seleccionar espacio activo", ft.Row([
            c_cselid,
            ft.Column([ft.Text("", size=11),
                       mk_btn("Activar espacio", sel_cfg_manual,
                               ft.Icons.RADIO_BUTTON_CHECKED)
                       ], spacing=4),
        ], spacing=12, wrap=True)),
        ct_cfg,
    ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)

    tab_amb = ft.Column([
        ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.SENSORS_OUTLINED, color=C_ACCENT, size=24),
                ft.Text(
                    "Datos del ambiente",
                    size=13, weight=ft.FontWeight.W_700, color=C_TEXT,
                ),
                ft.Text(
                    "Mediciones registradas automáticamente cada 50 segundos desde los sensores del Arduino.",
                    size=11, color=C_MUTED, italic=True,
                ),
            ], spacing=6),
            bgcolor=ft.Colors.with_opacity(0.05, C_ACCENT),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.2, C_ACCENT)),
            border_radius=8,
            padding=ft.Padding(left=12, top=10, right=12, bottom=10),
        ),
        sep_label("Resumen histórico por espacio"), ct_hamb,
        sep_label("Incidencias del espacio"),       ct_iesp,
        sep_label("Últimas mediciones registradas"), ct_mamb,
        sep_label("Incidencias por medicamento"),   ct_imed,
    ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)

    tab_resumen = ft.Column([
        ft.Row([
            mk_kpi_card("Medicamentos activos", kpi_meds, "Tabla medicamentos", ft.Icons.MEDICATION, C_ACCENT),
            mk_kpi_card("Stock bajo", kpi_stock, "Revisar reposición", ft.Icons.INVENTORY_2, C_WARN),
            mk_kpi_card("Avisos caducidad", kpi_cad, "No caducados", ft.Icons.EVENT_BUSY, C_WARN),
            mk_kpi_card("Incidencias abiertas", kpi_inc, "Espacio o medicamento", ft.Icons.WARNING_AMBER, C_ERR),
            mk_kpi_card("Alertas registradas", kpi_alertas, "Histórico de alertas", ft.Icons.NOTIFICATIONS_ACTIVE, C_INFO),
        ], spacing=12, wrap=True),
        ft.Row([
            ft.Container(
                content=ft.Column([sep_label("Medicamentos prioritarios"), ct_dash_meds], spacing=8, expand=True),
                expand=2,
            ),
            ft.Container(
                content=ft.Column([sep_label("Últimas alertas"), ct_dash_alert], spacing=8, expand=True),
                expand=2,
            ),
        ], spacing=12, expand=True),
        ft.Container(
            content=ft.Column([sep_label("Últimas mediciones ambientales"), ct_dash_med], spacing=8, expand=True),
            expand=True,
        ),
    ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)

    tab_alertas = ft.Column([
        ft.Row([
            badge_alarma,
            ft.Container(expand=True),
            mk_btn2("Actualizar alertas", lambda e: safe(cargar_alertas), ft.Icons.REFRESH),
        ]),
        ct_alert,
    ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO)

    def crear_tabs(items, secondary=False):
        """
        Crea pestañas compatibles con Flet 0.85.
        items: lista de pares (título, control_contenido).
        """
        return ft.Tabs(
            length=len(items),
            selected_index=0,
            expand=True,
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    ft.TabBar(
                        tabs=[ft.Tab(label=titulo) for titulo, _ in items],
                        scrollable=True,
                        secondary=secondary,
                        indicator_color=C_ACCENT,
                        label_color=C_TEXT,
                        unselected_label_color=C_MUTED,
                        divider_color=C_BORDER,
                    ),
                    ft.TabBarView(
                        expand=True,
                        controls=[contenido for _, contenido in items],
                    ),
                ],
            ),
        )

    tab_hist = crear_tabs([
        ("Histórico medicamentos", ct_hlogs),
        ("Movimientos stock",      ct_hmovs),
        ("Mediciones espacio",     ct_hmedc),
        ("Registro entrada",       ct_hregs),
    ], secondary=True)

    tab_terminal = ft.Column([
        ft.Container(
            content=ft.Row([
                mk_btn("Reconectar Arduino", reconectar, ft.Icons.CABLE),
                mk_btn2("Limpiar", lambda e: (terminal_clear(), page.update()),
                        ft.Icons.CLEAR),
            ], spacing=10),
            bgcolor=C_SURFACE,
            border_radius=8,
            padding=ft.Padding(left=12, top=8, right=12, bottom=8),
            border=ft.Border.all(1, C_BORDER),
        ),
        ft.Container(
            content=terminal_list,
            expand=True,
            bgcolor="#0a0f0a",
            border_radius=8,
            border=ft.Border.all(1, C_BORDER),
        ),
    ], spacing=8, expand=True)

    nav_tab_index = [1] 

    def on_nav_change(e):
        try:
            nav_tab_index[0] = int(e.data)
        except (TypeError, ValueError):
            nav_tab_index[0] = e.control.selected_index

    nav_tabbar = ft.TabBar(
        tabs=[
            ft.Tab(label="Resumen"),
            ft.Tab(label="Medicamentos"),
            ft.Tab(label="Stock"),
            ft.Tab(label="Caducidad"),
            ft.Tab(label="Alertas"),
            ft.Tab(label="Config. espacio"),
            ft.Tab(label="Mediciones esp."),
            ft.Tab(label="Histórico"),
            ft.Tab(label="Monitor serial"),
        ],
        scrollable=True,
        indicator_color=C_ACCENT,
        label_color=C_TEXT,
        unselected_label_color=C_MUTED,
        divider_color=C_BORDER,
    )

    nav = ft.Tabs(
        length=9,
        selected_index=1,
        on_change=on_nav_change,
        expand=True,
        content=ft.Column(
            expand=True,
            spacing=0,
            controls=[
                nav_tabbar,
                ft.TabBarView(
                    expand=True,
                    controls=[
                        ft.Container(content=tab_resumen,  padding=16, expand=True),
                        ft.Container(content=tab_meds,     padding=16, expand=True),
                        ft.Container(content=tab_stock,    padding=16, expand=True),
                        ft.Container(content=tab_cad,      padding=16, expand=True),
                        ft.Container(content=tab_alertas,  padding=16, expand=True),
                        ft.Container(content=tab_cfg,      padding=16, expand=True),
                        ft.Container(content=tab_amb,      padding=16, expand=True),
                        ft.Container(content=tab_hist,     padding=16, expand=True),
                        ft.Container(content=tab_terminal, padding=16, expand=True),
                    ],
                ),
            ],
        ),
    )

    top_bar = ft.Container(
        content=ft.Row([
            ft.Row([
                ft.Icon(ft.Icons.LOCAL_HOSPITAL, color=C_ACCENT, size=20),
                ft.Text("Pharma Control", size=15, weight=ft.FontWeight.W_700, color=C_TEXT),
                ft.Container(width=1, height=20, bgcolor=C_BORDER),
                lbl_cfg_act,
            ], spacing=10),
            ft.Row([
                sw_theme,
                mk_btn2("Actualizar", lambda e: refrescar_todo(), ft.Icons.REFRESH),
                lbl_status,
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        bgcolor=C_SURFACE, height=52,
        padding=ft.Padding(left=20, top=0, right=20, bottom=0),
        border=ft.Border.only(bottom=ft.BorderSide(1, C_BORDER)),
    )

    page.add(ft.Column([top_bar, nav], spacing=0, expand=True))

    threading.Thread(target=init_arduino, daemon=True).start()
    threading.Thread(target=lectura_serial, daemon=True).start()
    page.run_task(periodic_update)

    def cerrar_recursos():
        continuar.clear()
        if arduino and arduino.is_open:
            try:
                arduino.close()
            except Exception:
                pass

    def on_window_event(e):
        if getattr(e, "type", None) == ft.WindowEventType.CLOSE or str(getattr(e, "type", "")).lower() == "close":
            cerrar_recursos()

    page.window.on_event = on_window_event
    page.on_disconnect = lambda e: cerrar_recursos()


ft.run(main)
