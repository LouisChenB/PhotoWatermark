# -*- coding: utf-8 -*-
"""
watermarker.py
Windows GUI 批量图片加水印工具
依赖: PySide6, Pillow
界面中文，支持文本和 PNG 图片水印、透明度、缩放、旋转、拖拽、九宫格定位、模板保存/加载、批量导出等
"""
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QImage, QIcon, QFont, QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QSplitter, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsTextItem,
    QGraphicsItem, QSlider, QLineEdit, QColorDialog, QComboBox,
    QSpinBox, QCheckBox, QMessageBox, QGroupBox
)

APPDIR = Path.cwd()
TEMPLATES_DIR = APPDIR / "templates"
CONFIG_FILE = APPDIR / "config.json"
TEMPLATES_DIR.mkdir(exist_ok=True)


@dataclass
class WatermarkSettings:
    mode: str = "text"  # 'text' or 'image'
    text: str = "示例水印"
    font_file: Optional[str] = None  # 可选 ttf/otf 文件路径，优先使用
    font_family: str = "Arial"
    font_size: int = 48
    bold: bool = False
    italic: bool = False
    color: Tuple[int, int, int] = (255, 255, 255)
    opacity: float = 0.5  # 0-1
    shadow: bool = False
    shadow_offset: Tuple[int, int] = (2, 2)
    shadow_color: Tuple[int, int, int] = (0, 0, 0)
    outline: bool = False
    outline_width: int = 2
    outline_color: Tuple[int, int, int] = (0, 0, 0)
    image_path: Optional[str] = None  # 若为图片水印，此处为 PNG 路径
    image_scale: float = 0.25  # 相对于画布宽度的比例（如果 is_relative_scale True）
    image_scale_fixed: Tuple[int, int] = (100, 100)  # 固定像素大小（如果使用固定尺寸）
    use_relative_scale: bool = True
    position: Tuple[float, float] = (0.5, 0.5)  # 相对位置(0-1)，基于被处理图片
    rotation: float = 0.0  # 角度
    # export settings
    out_format: str = "PNG"  # PNG or JPEG
    jpeg_quality: int = 90  # 0-100
    resize_mode: str = "原始"  # '原始','按宽度','按高度','百分比'
    resize_value: int = 100  # px or percent
    filename_rule: str = "保留原文件名"  # '保留原文件名','添加前缀','添加后缀'
    filename_affix: str = "wm_"
    allow_export_to_source: bool = False


def pil_image_to_qpixmap(img: Image.Image) -> QPixmap:
    """Convert PIL Image to QPixmap"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def qpixmap_to_pil_image(qpix: QPixmap) -> Image.Image:
    """Convert QPixmap to PIL Image"""
    qimg = qpix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    width = qimg.width()
    height = qimg.height()
    ptr = qimg.bits()
    ptr.setsize(qimg.byteCount())
    arr = bytes(ptr)
    img = Image.frombytes("RGBA", (width, height), arr)
    return img


class DraggablePixmapItem(QGraphicsPixmapItem):
    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setTransformOriginPoint(self.boundingRect().center())

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        # we could notify parent or emit signal; main window will query position when exporting


class DraggableTextItem(QGraphicsTextItem):
    def __init__(self, text: str):
        super().__init__(text)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setDefaultTextColor(QColor(255, 255, 255))
        # use transform origin at center
        rect = self.boundingRect()
        self.setTransformOriginPoint(rect.center())


class WatermarkPreview(QGraphicsView):
    """显示原图与水印，可拖拽水印"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.base_pixmap_item: Optional[QGraphicsPixmapItem] = None
        self.wm_item: Optional[QGraphicsItem] = None
        self.current_image_pil: Optional[Image.Image] = None
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumSize(600, 400)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def load_image(self, pil_img: Image.Image):
        self.scene().clear()
        self.current_image_pil = pil_img.copy()
        qpix = pil_image_to_qpixmap(pil_img if pil_img.mode in ("RGB", "RGBA") else pil_img.convert("RGBA"))
        self.base_pixmap_item = QGraphicsPixmapItem(qpix)
        self.base_pixmap_item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.scene().addItem(self.base_pixmap_item)
        self.setSceneRect(self.base_pixmap_item.boundingRect())
        self.fitInView(self.base_pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def ensure_wm_item(self, mode='text', text='水印', pixmap: Optional[QPixmap] = None, font: Optional[QFont] = None):
        # remove existing wm_item then add new
        if self.wm_item:
            try:
                self.scene().removeItem(self.wm_item)
            except Exception:
                pass
            self.wm_item = None
        if mode == 'text':
            item = DraggableTextItem(text)
            if font:
                item.setFont(font)
            item.setDefaultTextColor(QColor(255, 255, 255))
            self.wm_item = item
            # set origin to center after text bounding rect resolves
            self.scene().addItem(self.wm_item)
        else:
            if pixmap is None:
                pixmap = QPixmap(100, 100)
            item = DraggablePixmapItem(pixmap)
            self.wm_item = item
            self.scene().addItem(self.wm_item)
        # put watermark at center by default
        if self.base_pixmap_item:
            base_rect = self.base_pixmap_item.boundingRect()
            wm_rect = self.wm_item.boundingRect()
            cx = base_rect.width() / 2 - wm_rect.width() / 2
            cy = base_rect.height() / 2 - wm_rect.height() / 2
            self.wm_item.setPos(cx, cy)
            self.wm_item.setZValue(10)

    def update_text_item_style(self, font: QFont, color: QColor, opacity: float, outline: bool = False,
                               outline_width: int = 2, shadow: bool = False):
        if not isinstance(self.wm_item, DraggableTextItem):
            return
        self.wm_item.setFont(font)
        self.wm_item.setDefaultTextColor(color)
        self.wm_item.setOpacity(opacity)
        # outline/shadow not rendered here as QGraphicsTextItem; preview approximate by drawing a shadow pixmap
        # For simplicity, leave detailed effects to final PIL rendering.

    def update_pixmap_item_style(self, pixmap: QPixmap, opacity: float, scale: float, rotation: float):
        if not isinstance(self.wm_item, DraggablePixmapItem):
            return
        if pixmap:
            # scale pixmap to requested factor
            w = pixmap.width()
            h = pixmap.height()
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            scaled = pixmap.scaled(new_w, new_h, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            self.wm_item.setPixmap(scaled)
            self.wm_item.setOpacity(opacity)
            self.wm_item.setRotation(rotation)

    def get_wm_relative_position(self) -> Tuple[float, float]:
        """Return watermark's normalized position relative to base image (0-1 center)"""
        if not (self.base_pixmap_item and self.wm_item):
            return 0.5, 0.5
        base_rect = self.base_pixmap_item.boundingRect()
        wm_rect = self.wm_item.boundingRect()
        pos = self.wm_item.pos()
        center_x = pos.x() + wm_rect.width() / 2
        center_y = pos.y() + wm_rect.height() / 2
        rel_x = float(center_x / base_rect.width())
        rel_y = float(center_y / base_rect.height())
        # clamp
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        return rel_x, rel_y

    def get_wm_size_ratio(self) -> Tuple[float, float]:
        """Return watermark size relative to base image (w_ratio,h_ratio)"""
        if not (self.base_pixmap_item and self.wm_item):
            return 0.1, 0.1
        base_rect = self.base_pixmap_item.boundingRect()
        wm_rect = self.wm_item.boundingRect()
        return float(wm_rect.width() / base_rect.width()), float(wm_rect.height() / base_rect.height())

    def set_view_scale_mode(self):
        # called after adding items; ensure fit
        if self.base_pixmap_item:
            self.fitInView(self.base_pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("图片批量加水印 - 中文界面")
        self.resize(1200, 800)

        self.settings = WatermarkSettings()
        self.imported_files: List[Path] = []
        self.current_index: int = -1

        self._load_config()
        self._build_ui()
        self._connect_signals()

    def closeEvent(self, event):
        self._save_config()
        super().closeEvent(event)

    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for k, v in data.get("settings", {}).items():
                        if hasattr(self.settings, k):
                            setattr(self.settings, k, v)
            except Exception:
                pass

    def _save_config(self):
        try:
            data = {"settings": asdict(self.settings)}
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 修改 _build_ui 方法，调整布局结构和按钮位置
    
    def _build_ui(self):
        # central widgets
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
    
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
    
        # left: file list + import buttons + template controls + export settings + export buttons
        left = QWidget()
        left_l = QVBoxLayout(left)
        
        # 导入部分
        self.btn_add_files = QPushButton("导入图片/文件（支持多选）")
        self.btn_add_folder = QPushButton("导入文件夹")
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(140, 80))
        self.lbl_files_count = QLabel("已导入：0 张")
        left_l.addWidget(self.btn_add_files)
        left_l.addWidget(self.btn_add_folder)
        left_l.addWidget(self.list_widget)
        left_l.addWidget(self.lbl_files_count)
        
        # 模板控制部分（放在导入和导出中间）
        template_group = QGroupBox("模板管理")
        tpl_l = QHBoxLayout(template_group)
        self.btn_save_template = QPushButton("保存当前模板")
        self.combo_templates = QComboBox()
        self._refresh_template_list()
        self.btn_load_template = QPushButton("加载模板")
        self.btn_delete_template = QPushButton("删除模板")
        tpl_l.addWidget(self.btn_save_template)
        tpl_l.addWidget(self.combo_templates)
        tpl_l.addWidget(self.btn_load_template)
        tpl_l.addWidget(self.btn_delete_template)
        left_l.addWidget(template_group)
        
        # 导出设置部分
        export_group = QGroupBox("导出设置")
        ex_l = QVBoxLayout(export_group)
        format_row = QWidget()
        fr_l = QHBoxLayout(format_row)
        fr_l.addWidget(QLabel("输出格式"))
        self.combo_outformat = QComboBox()
        self.combo_outformat.addItems(["PNG", "JPEG"])
        self.combo_outformat.setCurrentText(self.settings.out_format)
        fr_l.addWidget(self.combo_outformat)
        fr_l.addWidget(QLabel("JPEG质量"))
        self.slider_quality = QSlider(Qt.Orientation.Horizontal)
        self.slider_quality.setRange(1, 100)
        self.slider_quality.setValue(self.settings.jpeg_quality)
        fr_l.addWidget(self.slider_quality)
        ex_l.addWidget(format_row)
    
        rename_row = QWidget()
        rn_l = QHBoxLayout(rename_row)
        self.combo_namerule = QComboBox()
        self.combo_namerule.addItems(["保留原文件名", "添加前缀", "添加后缀"])
        self.combo_namerule.setCurrentText(self.settings.filename_rule)
        rn_l.addWidget(self.combo_namerule)
        self.line_affix = QLineEdit(self.settings.filename_affix)
        rn_l.addWidget(self.line_affix)
        ex_l.addWidget(rename_row)
    
        resize_row = QWidget()
        rz_l = QHBoxLayout(resize_row)
        rz_l.addWidget(QLabel("导出尺寸"))
        self.combo_resize_mode = QComboBox()
        self.combo_resize_mode.addItems(["原始", "按宽度", "按高度", "百分比"])
        self.combo_resize_mode.setCurrentText(self.settings.resize_mode)
        self.spin_resize_value = QSpinBox()
        self.spin_resize_value.setRange(1, 10000)
        self.spin_resize_value.setValue(self.settings.resize_value)
        rz_l.addWidget(self.combo_resize_mode)
        rz_l.addWidget(self.spin_resize_value)
        ex_l.addWidget(resize_row)
    
        self.chk_allow_export_same_folder = QCheckBox("允许导出到原文件夹（可能覆盖原图）")
        self.chk_allow_export_same_folder.setChecked(self.settings.allow_export_to_source)
        ex_l.addWidget(self.chk_allow_export_same_folder)
        
        left_l.addWidget(export_group)
        
        # 应用和导出按钮（放在导出部分下方）
        action_buttons = QWidget()
        ab_l = QHBoxLayout(action_buttons)
        self.btn_preview_apply = QPushButton("应用到预览")
        self.btn_export = QPushButton("批量导出")
        ab_l.addWidget(self.btn_preview_apply)
        ab_l.addWidget(self.btn_export)
        left_l.addWidget(action_buttons)
    
        # right: preview + other controls (without export settings and buttons)
        right = QWidget()
        right_l = QVBoxLayout(right)
    
        # preview
        self.preview = WatermarkPreview()
        right_l.addWidget(self.preview)
    
        # controls area (grouped)
        controls = QWidget()
        controls_l = QHBoxLayout(controls)
    
        # watermark type & basic
        group_basic = QGroupBox("水印类型与基本设置")
        gb_l = QVBoxLayout(group_basic)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["文本水印", "图片水印"])
        gb_l.addWidget(self.combo_mode)
    
        # text controls
        self.text_input = QLineEdit(self.settings.text)
        gb_l.addWidget(QLabel("水印文字："))
        gb_l.addWidget(self.text_input)
    
        font_row = QWidget()
        font_row_l = QHBoxLayout(font_row)
        self.btn_choose_fontfile = QPushButton("选择字体文件(.ttf/.otf)")
        self.lbl_fontfile = QLabel(self.settings.font_file or "未选择字体文件")
        font_row_l.addWidget(self.btn_choose_fontfile)
        font_row_l.addWidget(self.lbl_fontfile)
        gb_l.addWidget(font_row)
    
        font_opts = QWidget()
        fo_l = QHBoxLayout(font_opts)
        fo_l.addWidget(QLabel("字号"))
        self.spin_fontsize = QSpinBox()
        self.spin_fontsize.setRange(8, 400)
        self.spin_fontsize.setValue(self.settings.font_size)
        fo_l.addWidget(self.spin_fontsize)
        self.chk_bold = QCheckBox("加粗")
        self.chk_bold.setChecked(self.settings.bold)
        fo_l.addWidget(self.chk_bold)
        self.chk_italic = QCheckBox("斜体")
        self.chk_italic.setChecked(self.settings.italic)
        fo_l.addWidget(self.chk_italic)
        gb_l.addWidget(font_opts)
    
        # color/opacity/shadow/outline
        col_row = QWidget()
        col_row_l = QHBoxLayout(col_row)
        self.btn_color = QPushButton("选择文字颜色")
        self.lbl_color = QLabel()
        self.lbl_color.setFixedSize(36, 16)
        # 修复第328行附近的颜色样式表设置
        r, g, b = self.settings.color
        self.lbl_color.setStyleSheet(f"background: rgb({r}, {g}, {b});")
        col_row_l.addWidget(self.btn_color)
        col_row_l.addWidget(self.lbl_color)
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(0, 100)
        self.slider_opacity.setValue(int(self.settings.opacity * 100))
        col_row_l.addWidget(QLabel("透明度"))
        col_row_l.addWidget(self.slider_opacity)
        gb_l.addWidget(col_row)
    
        self.chk_shadow = QCheckBox("启用阴影")
        self.chk_shadow.setChecked(self.settings.shadow)
        self.chk_outline = QCheckBox("启用描边")
        self.chk_outline.setChecked(self.settings.outline)
        gb_l.addWidget(self.chk_shadow)
        gb_l.addWidget(self.chk_outline)
    
        # image watermark controls
        img_group = QGroupBox("图片水印（PNG 推荐，支持透明）")
        img_l = QVBoxLayout(img_group)
        self.btn_choose_wm_image = QPushButton("选择 PNG 水印图片")
        self.lbl_wm_image = QLabel(self.settings.image_path or "未选择")
        img_l.addWidget(self.btn_choose_wm_image)
        img_l.addWidget(self.lbl_wm_image)
        img_scale_row = QWidget()
        isr_l = QHBoxLayout(img_scale_row)
        self.slider_img_scale = QSlider(Qt.Orientation.Horizontal)
        self.slider_img_scale.setRange(1, 200)
        self.slider_img_scale.setValue(int(self.settings.image_scale * 100))
        isr_l.addWidget(QLabel("缩放(相对宽度%)"))
        isr_l.addWidget(self.slider_img_scale)
        img_l.addWidget(img_scale_row)
    
        # layout and position
        pos_group = QGroupBox("位置与旋转")
        pos_l = QVBoxLayout(pos_group)
        # nine-grid buttons
        grid_row = QWidget()
        grid_l = QHBoxLayout(grid_row)
        self.btn_grid = {}
        labels = ["左上", "上中", "右上", "左中", "居中", "右中", "左下", "下中", "右下"]
        for lab in labels:
            b = QPushButton(lab)
            b.setFixedWidth(60)
            grid_l.addWidget(b)
            self.btn_grid[lab] = b
        pos_l.addWidget(grid_row)
        pos_l.addWidget(QLabel("也可以直接在预览图上拖拽水印到任意位置"))
        rot_row = QWidget()
        rot_l = QHBoxLayout(rot_row)
        rot_l.addWidget(QLabel("旋转(度)"))
        self.slider_rotation = QSlider(Qt.Orientation.Horizontal)
        self.slider_rotation.setRange(0, 360)
        self.slider_rotation.setValue(int(self.settings.rotation))
        rot_l.addWidget(self.slider_rotation)
        pos_l.addWidget(rot_row)
    
        # 右侧控制面板不包含导出设置和按钮
        controls_l.addWidget(group_basic)
        controls_l.addWidget(img_group)
        controls_l.addWidget(pos_group)
    
        right_l.addWidget(controls)
        splitter.addWidget(left)
        splitter.addWidget(right)
    
        # status bar
        self.status = self.statusBar()

    def _connect_signals(self):
        self.btn_add_files.clicked.connect(self.on_add_files)
        self.btn_add_folder.clicked.connect(self.on_add_folder)
        self.list_widget.itemClicked.connect(self.on_select_list_item)
        self.btn_choose_fontfile.clicked.connect(self.on_choose_fontfile)
        self.btn_color.clicked.connect(self.on_choose_color)
        self.btn_choose_wm_image.clicked.connect(self.on_choose_wm_image)
        self.combo_mode.currentIndexChanged.connect(self.on_mode_changed)
        self.btn_preview_apply.clicked.connect(self.on_apply_preview)
        self.btn_export.clicked.connect(self.on_export)
        self.slider_img_scale.valueChanged.connect(lambda _: self.on_apply_preview())
        self.slider_opacity.valueChanged.connect(lambda _: self.on_apply_preview())
        self.spin_fontsize.valueChanged.connect(lambda _: self.on_apply_preview())
        self.chk_bold.stateChanged.connect(lambda _: self.on_apply_preview())
        self.chk_italic.stateChanged.connect(lambda _: self.on_apply_preview())
        self.slider_rotation.valueChanged.connect(lambda _: self.on_apply_preview())
        self.btn_grid["左上"].clicked.connect(lambda: self._set_position_by_grid("左上"))
        self.btn_grid["上中"].clicked.connect(lambda: self._set_position_by_grid("上中"))
        self.btn_grid["右上"].clicked.connect(lambda: self._set_position_by_grid("右上"))
        self.btn_grid["左中"].clicked.connect(lambda: self._set_position_by_grid("左中"))
        self.btn_grid["居中"].clicked.connect(lambda: self._set_position_by_grid("居中"))
        self.btn_grid["右中"].clicked.connect(lambda: self._set_position_by_grid("右中"))
        self.btn_grid["左下"].clicked.connect(lambda: self._set_position_by_grid("左下"))
        self.btn_grid["下中"].clicked.connect(lambda: self._set_position_by_grid("下中"))
        self.btn_grid["右下"].clicked.connect(lambda: self._set_position_by_grid("右下"))

        self.btn_save_template.clicked.connect(self.on_save_template)
        self.btn_load_template.clicked.connect(self.on_load_template)
        self.btn_delete_template.clicked.connect(self.on_delete_template)

    # ---------- 文件导入相关 ----------
    def on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片文件", str(APPDIR), "图片 (*.png *.jpg *.jpeg *.bmp)")
        if not files:
            return
        for f in files:
            p = Path(f)
            if p not in self.imported_files:
                self.imported_files.append(p)
        self._refresh_file_list()

    def on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", str(APPDIR))
        if not folder:
            return
        folder = Path(folder)
        exts = ('.png', '.jpg', '.jpeg', '.bmp')
        for p in folder.rglob('*'):
            if p.suffix.lower() in exts:
                if p not in self.imported_files:
                    self.imported_files.append(p)
        self._refresh_file_list()

    def _refresh_file_list(self):
        self.list_widget.clear()
        for p in self.imported_files:
            # generate thumbnail
            try:
                img = Image.open(p)
                img.thumbnail((280, 160))
                pix = pil_image_to_qpixmap(img)
                item = QListWidgetItem(QIcon(pix), p.name)
                item.setData(Qt.ItemDataRole.UserRole, str(p))
                self.list_widget.addItem(item)
            except Exception as e:
                print("缩略图生成失败:", e)
        self.lbl_files_count.setText(f"已导入：{len(self.imported_files)} 张")

    def on_select_list_item(self, item: QListWidgetItem):
        p = Path(item.data(Qt.ItemDataRole.UserRole))
        self._show_image_in_preview(p)
        self.current_index = self.imported_files.index(p)

    def _show_image_in_preview(self, p: Path):
        try:
            img = Image.open(p).convert("RGBA")
            self.preview.load_image(img)
            # ensure watermark item exists
            if self.settings.mode == "text":
                font = QFont(self.settings.font_family, self.settings.font_size)
                font.setBold(self.settings.bold)
                font.setItalic(self.settings.italic)
                self.preview.ensure_wm_item(mode='text', text=self.settings.text, font=font)
                qcol = QColor(*self.settings.color)
                self.preview.update_text_item_style(font, qcol, self.settings.opacity,
                                                    outline=self.settings.outline,
                                                    outline_width=self.settings.outline_width,
                                                    shadow=self.settings.shadow)
            else:
                # load watermark pixmap
                if self.settings.image_path and Path(self.settings.image_path).exists():
                    pil_wm = Image.open(self.settings.image_path).convert("RGBA")
                    pix = pil_image_to_qpixmap(pil_wm)
                    # compute scale factor for preview: relative to base width
                    scale = self.settings.image_scale if self.settings.use_relative_scale else (
                                self.settings.image_scale_fixed[0] / pix.width)
                    self.preview.ensure_wm_item(mode='image', pixmap=pix)
                    self.preview.update_pixmap_item_style(pixmap=pix, opacity=self.settings.opacity, scale=scale,
                                                          rotation=self.settings.rotation)
            self.preview.set_view_scale_mode()
        except Exception as e:
            QMessageBox.warning(self, "打开失败", f"无法打开图片：{e}")

    # ---------- 字体/颜色/水印图片 ----------
    def on_choose_fontfile(self):
        file, _ = QFileDialog.getOpenFileName(self, "选择字体文件", str(APPDIR), "字体文件 (*.ttf *.otf)")
        if not file:
            return
        self.settings.font_file = file
        self.lbl_fontfile.setText(str(file))
        self.on_apply_preview()

    def on_choose_color(self):
        col = QColorDialog.getColor()
        if not col.isValid():
            return
        self.settings.color = (col.red(), col.green(), col.blue())
        self.lbl_color.setStyleSheet(f"background: rgb{self.settings.color};")
        self.on_apply_preview()

    def on_choose_wm_image(self):
        file, _ = QFileDialog.getOpenFileName(self, "选择 PNG 水印（推荐透明 PNG）", str(APPDIR), "PNG 图片 (*.png)")
        if not file:
            return
        self.settings.image_path = file
        self.lbl_wm_image.setText(file)
        self.on_apply_preview()

    def on_mode_changed(self, idx):
        mode = "text" if idx == 0 else "image"
        self.settings.mode = mode
        self.on_apply_preview()

    # ---------- 预览应用 ----------
    def on_apply_preview(self):
        # update settings from UI
        self.settings.text = self.text_input.text()
        self.settings.font_size = self.spin_fontsize.value()
        self.settings.bold = self.chk_bold.isChecked()
        self.settings.italic = self.chk_italic.isChecked()
        self.settings.opacity = self.slider_opacity.value() / 100.0
        self.settings.image_scale = self.slider_img_scale.value() / 100.0
        self.settings.rotation = float(self.slider_rotation.value())
        self.settings.font_family = "Arial"  # placeholder; advanced: get from QFontComboBox
        # if no file selected and mode text and current preview exists -> create text item
        if self.current_index >= 0:
            p = self.imported_files[self.current_index]
            self._show_image_in_preview(p)
        else:
            # if no image loaded, but there are imported files, select first
            if self.imported_files:
                self.current_index = 0
                self._show_image_in_preview(self.imported_files[0])

    def _set_position_by_grid(self, key):
        mapping = {
            "左上": (0.1, 0.1),
            "上中": (0.5, 0.1),
            "右上": (0.9, 0.1),
            "左中": (0.1, 0.5),
            "居中": (0.5, 0.5),
            "右中": (0.9, 0.5),
            "左下": (0.1, 0.9),
            "下中": (0.5, 0.9),
            "右下": (0.9, 0.9),
        }
        if key in mapping:
            self.settings.position = mapping[key]
            # move preview item if exists
            if self.preview.wm_item and self.preview.base_pixmap_item:
                base = self.preview.base_pixmap_item.boundingRect()
                wm = self.preview.wm_item.boundingRect()
                cx = mapping[key][0] * base.width() - wm.width() / 2
                cy = mapping[key][1] * base.height() - wm.height() / 2
                self.preview.wm_item.setPos(cx, cy)

    # ---------- 模板管理 ----------
    def _refresh_template_list(self):
        self.combo_templates.clear()
        tpl_files = sorted(TEMPLATES_DIR.glob("*.json"))
        self.combo_templates.addItem("（选择模板）")
        for f in tpl_files:
            self.combo_templates.addItem(f.stem)

    def on_save_template(self):
        name, ok = QFileDialog.getSaveFileName(self, "保存模板为 JSON（输入模板名）", str(TEMPLATES_DIR), "模板 (*.json)")
        if not name:
            return
        try:
            s = asdict(self.settings)

            # convert tuples to lists for JSON
            def prepare(obj):
                if isinstance(obj, tuple):
                    return list(obj)
                return obj

            s = {k: prepare(v) for k, v in s.items()}
            with open(name, 'w', encoding='utf-8') as f:
                json.dump(s, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "保存成功", "模板已保存")
            self._refresh_template_list()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def on_load_template(self):
        sel = self.combo_templates.currentText()
        if not sel or sel == "（选择模板）":
            QMessageBox.information(self, "请选择模板", "请先从下拉列表选择一个模板")
            return
        path = TEMPLATES_DIR / f"{sel}.json"
        if not path.exists():
            QMessageBox.warning(self, "模板不存在", "找不到模板文件")
            self._refresh_template_list()
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(self.settings, k):
                    setattr(self.settings, k, v)
            # update UI controls accordingly (some)
            self.text_input.setText(self.settings.text)
            self.spin_fontsize.setValue(self.settings.font_size)
            self.chk_bold.setChecked(bool(self.settings.bold))
            self.chk_italic.setChecked(bool(self.settings.italic))
            self.slider_opacity.setValue(int(self.settings.opacity * 100))
            self.slider_img_scale.setValue(int(self.settings.image_scale * 100))
            self.slider_rotation.setValue(int(self.settings.rotation))
            self.lbl_fontfile.setText(self.settings.font_file or "未选择字体文件")
            self.lbl_wm_image.setText(self.settings.image_path or "未选择")
            QMessageBox.information(self, "加载成功", "模板已加载（仅更新 UI，需点击 应用到预览 生效）")
        except Exception as e:
            QMessageBox.warning(self, "加载失败", str(e))

    def on_delete_template(self):
        sel = self.combo_templates.currentText()
        if not sel or sel == "（选择模板）":
            QMessageBox.information(self, "请选择模板", "请先从下拉列表选择一个模板")
            return
        path = TEMPLATES_DIR / f"{sel}.json"
        if path.exists():
            confirm = QMessageBox.question(self, "删除确认", f"确定删除模板 {sel} 吗？",
                                           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if confirm == QMessageBox.StandardButton.Yes:
                path.unlink()
                QMessageBox.information(self, "删除成功", "模板已删除")
                self._refresh_template_list()

    # ---------- 导出逻辑 ----------
    def on_export(self):
        if not self.imported_files:
            QMessageBox.information(self, "无图片", "请先导入图片")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "选择输出文件夹", str(APPDIR))
        if not out_dir:
            return
        out_dir = Path(out_dir)
        if (not self.chk_allow_export_same_folder.isChecked()) and any(
                p.parent == out_dir for p in self.imported_files):
            QMessageBox.warning(self, "禁止导出到原文件夹",
                                "为防止覆盖原图，当前设置禁止导出到原文件夹。如需导出到原文件夹，请勾选允许导出到原文件夹。")
            return
        # update settings from UI (export controls)
        self.settings.out_format = self.combo_outformat.currentText()
        self.settings.jpeg_quality = self.slider_quality.value()
        self.settings.filename_rule = self.combo_namerule.currentText()
        self.settings.filename_affix = self.line_affix.text()
        self.settings.resize_mode = self.combo_resize_mode.currentText()
        self.settings.resize_value = self.spin_resize_value.value()
        self.settings.allow_export_to_source = self.chk_allow_export_same_folder.isChecked()

        n = len(self.imported_files)
        self.status.showMessage(f"开始导出 {n} 张图片 ...")
        failed = []
        for i, p in enumerate(self.imported_files, start=1):
            try:
                self._export_single(p, out_dir)
                self.status.showMessage(f"已导出 {i}/{n}")
                QApplication.processEvents()
            except Exception as e:
                failed.append((p, str(e)))
        if failed:
            msg = "部分导出失败：\n" + "\n".join([f"{p}: {e}" for p, e in failed])
            QMessageBox.warning(self, "导出完成（部分失败）", msg)
        else:
            QMessageBox.information(self, "导出完成", f"已成功导出 {n} 张图片")
        self.status.clearMessage()

    def _export_single(self, src: Path, out_dir: Path):
        # open image
        img = Image.open(src).convert("RGBA")
        iw, ih = img.size

        # prepare watermark image (RGBA)
        if self.settings.mode == "text":
            wm = self._render_text_watermark_image(iw, ih)
        else:
            wm = self._render_image_watermark_image(iw, ih)

        # position: compute top-left so that wm center matches settings.position of image
        posx = int(self.settings.position[0] * iw - wm.width / 2)
        posy = int(self.settings.position[1] * ih - wm.height / 2)

        # compose
        base = img.copy()
        base.paste(wm, (posx, posy), wm)  # wm as mask uses alpha channel

        # resize if needed
        mode = self.settings.resize_mode
        if mode == "原始":
            out_img = base
        elif mode == "按宽度":
            target_w = int(self.settings.resize_value)
            ratio = target_w / base.width
            target_h = int(base.height * ratio)
            out_img = base.resize((target_w, target_h), Image.LANCZOS)
        elif mode == "按高度":
            target_h = int(self.settings.resize_value)
            ratio = target_h / base.height
            target_w = int(base.width * ratio)
            out_img = base.resize((target_w, target_h), Image.LANCZOS)
        else:  # 百分比
            pct = self.settings.resize_value / 100.0
            target_w = max(1, int(base.width * pct))
            target_h = max(1, int(base.height * pct))
            out_img = base.resize((target_w, target_h), Image.LANCZOS)

        # output filename rule
        name = src.stem
        ext = self.settings.out_format.lower()
        if self.settings.filename_rule == "保留原文件名":
            out_name = name
        elif self.settings.filename_rule == "添加前缀":
            out_name = f"{self.settings.filename_affix}{name}"
        else:
            out_name = f"{name}{self.settings.filename_affix}"
        out_path = out_dir / f"{out_name}.{ext if ext != 'jpeg' else 'jpg'}"
        # save
        if self.settings.out_format == "PNG":
            out_img.save(out_path, format="PNG")
        else:
            # JPEG: must convert to RGB; handle quality
            rgb = out_img.convert("RGB")
            rgb.save(out_path, format="JPEG", quality=self.settings.jpeg_quality)

    def _render_text_watermark_image(self, base_w: int, base_h: int) -> Image.Image:
        """
        Render text watermark as an RGBA image sized appropriately.
        Strategy:
         - choose font size relative to base or fixed
         - draw text on transparent canvas, apply outline/shadow/opacity/rotation
        """
        txt = self.settings.text or ""
        # choose font
        font_path = None
        if self.settings.font_file and Path(self.settings.font_file).exists():
            font_path = self.settings.font_file
        else:
            # try to use default system font via PIL (may not support chinese)
            try:
                font = ImageFont.truetype("arial.ttf", max(10, int(self.settings.font_size)))
            except Exception:
                font = ImageFont.load_default()
                # fallback
        if font_path:
            font = ImageFont.truetype(font_path, max(8, int(self.settings.font_size)))
        # determine size
        # Large canvas ensuring room for rotation
        tmp_img = Image.new("RGBA", (base_w, base_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tmp_img)
        left, top, right, bottom = font.getbbox(txt)
        text_w, text_h = right - left, bottom - top
        pad = int(self.settings.outline_width * 2 + 10)
        canvas_w = text_w + pad * 2
        canvas_h = text_h + pad * 2
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)
        x = pad
        y = pad
        # shadow
        if self.settings.shadow:
            sx, sy = self.settings.shadow_offset
            d.text((x + sx, y + sy), txt, font=font,
                   fill=(*self.settings.shadow_color, int(255 * self.settings.opacity)))
        # outline: draw multiple offsets
        if self.settings.outline and self.settings.outline_width > 0:
            ow = max(1, int(self.settings.outline_width))
            for ox in range(-ow, ow + 1):
                for oy in range(-ow, ow + 1):
                    if ox == 0 and oy == 0: continue
                    d.text((x + ox, y + oy), txt, font=font,
                           fill=(*self.settings.outline_color, int(255 * self.settings.opacity)))
        # main text
        d.text((x, y), txt, font=font, fill=(*self.settings.color, int(255 * self.settings.opacity)))
        # rotate
        if abs(self.settings.rotation) > 0.01:
            canvas = canvas.rotate(-self.settings.rotation, expand=True)
        return canvas

    def _render_image_watermark_image(self, base_w: int, base_h: int) -> Image.Image:
        # load watermark image
        if not self.settings.image_path:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        im = Image.open(self.settings.image_path).convert("RGBA")
        # determine scale
        if self.settings.use_relative_scale:
            # relative to base width
            target_w = int(base_w * self.settings.image_scale)
            ratio = target_w / im.width
            target_h = max(1, int(im.height * ratio))
        else:
            target_w, target_h = self.settings.image_scale_fixed
        im = im.resize((target_w, target_h), Image.LANCZOS)
        # apply overall opacity
        if self.settings.opacity < 0.999:
            alpha = im.split()[3]
            alpha = ImageEnhance.Brightness(alpha).enhance(self.settings.opacity)
            im.putalpha(alpha)
        # rotation
        if abs(self.settings.rotation) > 0.01:
            im = im.rotate(-self.settings.rotation, expand=True)
        return im


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
