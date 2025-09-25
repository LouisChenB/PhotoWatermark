import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QPointF, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QIcon, QFontDatabase
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QSlider, QSpinBox, QComboBox,
    QGroupBox, QLineEdit, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsTextItem, QTabWidget, QMessageBox, QColorDialog, QCheckBox
)

APP_DATA_DIR = Path(os.path.expanduser('~')) / '.watermarker_py'
TEMPLATES_FILE = APP_DATA_DIR / 'templates.json'
LAST_SETTINGS_FILE = APP_DATA_DIR / 'last_settings.json'

SUPPORTED_INPUT = ('.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG')


# --------------------------- Helpers ---------------------------

def ensure_app_dir():
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path):
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_system_font_path(family_name):
    """尝试在常见系统字体目录中找到 family 对应的 ttf 文件路径（Windows）"""
    # 通常 Windows 字体放在 C:\Windows\Fonts
    fonts_dir = Path(os.environ.get('WINDIR', 'C:\\Windows')) / 'Fonts'
    if not fonts_dir.exists():
        # fallback: search common places
        fonts_dir = Path('/usr/share/fonts')
    family_lower = family_name.lower()
    for f in fonts_dir.glob('**/*'):
        if f.suffix.lower() in ('.ttf', '.otf'):
            name = f.stem.lower()
            if family_lower in name:
                return str(f)
    # not found -> return None
    return None


def pil_image_to_qpixmap(im: Image.Image) -> QPixmap:
    if im.mode not in ('RGBA', 'RGB'):
        im = im.convert('RGBA')
    data = im.tobytes('raw', 'RGBA')
    qimg = QImage(data, im.width, im.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def qpixmap_to_pil(qpixmap: QPixmap) -> Image.Image:
    qimg = qpixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
    width = qimg.width()
    height = qimg.height()
    ptr = qimg.bits()
    ptr.setsize(qimg.byteCount())
    arr = bytes(ptr)
    im = Image.frombuffer('RGBA', (width, height), arr, 'raw', 'RGBA', 0, 1)
    return im


# --------------------------- Graphics Items ---------------------------

class DraggableTextItem(QGraphicsTextItem):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFlag(QGraphicsTextItem.ItemIsMovable, True)
        self.setFlag(QGraphicsTextItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setDefaultTextColor(QtGui.QColor(255, 255, 255))
        self._rotation = 0.0

    def set_rotation(self, deg):
        self._rotation = deg
        self.setRotation(deg)


class DraggablePixmapItem(QGraphicsPixmapItem):
    def __init__(self, pixmap, parent=None):
        super().__init__(pixmap, parent)
        self.setFlag(QGraphicsPixmapItem.ItemIsMovable, True)
        self.setFlag(QGraphicsPixmapItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._rotation = 0.0

    def set_rotation(self, deg):
        self._rotation = deg
        self.setRotation(deg)


class DragDropListWidget(QListWidget):
    """支持从资源管理器拖拽文件/文件夹到列表的 QListWidget 子类。
    发射 filesDropped(list_of_paths) 信号，路径已经展开为图片文件路径列表。"""
    filesDropped = pyqtSignal(list)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 接受拖放
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls]
        files = []
        for p in paths:
            if os.path.isdir(p):
                for root, _, filenames in os.walk(p):
                    for fn in filenames:
                        if fn.lower().endswith(SUPPORTED_INPUT):
                            files.append(os.path.join(root, fn))
            elif os.path.isfile(p) and p.lower().endswith(SUPPORTED_INPUT):
                files.append(p)
        if files:
            # 发射已经展开并过滤过的文件路径列表
            self.filesDropped.emit(files)
        event.acceptProposedAction()


# --------------------------- Main App ---------------------------

class WatermarkerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_app_dir()
        self.setWindowTitle('水印工具 - 本地 (Windows)')
        self.resize(1200, 800)

        self.images = []  # list of file paths
        self.current_index = None

        self.templates = load_json(TEMPLATES_FILE) or {}
        self.last_settings = load_json(LAST_SETTINGS_FILE) or {}

        self._build_ui()
        self._load_last_settings()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)

        # ---------- 左侧: 导入/文件列表 / 导出设置 ----------
        left_col = QVBoxLayout()
        import_group = QGroupBox('导入图片')
        ig_layout = QVBoxLayout()

        btn_add_files = QPushButton('添加图片')
        btn_add_folder = QPushButton('导入文件夹')
        btn_clear = QPushButton('清空列表')
        ig_layout.addWidget(btn_add_files)
        ig_layout.addWidget(btn_add_folder)
        ig_layout.addWidget(btn_clear)

        self.list_widget = DragDropListWidget()
        self.list_widget.setIconSize(QtCore.QSize(120, 80))
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        ig_layout.addWidget(self.list_widget)
        import_group.setLayout(ig_layout)

        left_col.addWidget(import_group, 6)

        export_group = QGroupBox('导出设置')
        eg_layout = QVBoxLayout()

        # 输出文件夹
        out_layout = QHBoxLayout()
        self.out_folder_edit = QLineEdit()
        btn_choose_out = QPushButton('选择输出文件夹')
        out_layout.addWidget(self.out_folder_edit)
        out_layout.addWidget(btn_choose_out)
        eg_layout.addLayout(out_layout)

        # 防止覆盖选项
        self.chk_prevent_overwrite = QCheckBox('禁止导出到原文件夹（默认开启）')
        self.chk_prevent_overwrite.setChecked(True)
        eg_layout.addWidget(self.chk_prevent_overwrite)

        # 命名规则
        name_layout = QHBoxLayout()
        self.name_rule_combo = QComboBox()
        self.name_rule_combo.addItems(['保留原文件名', '添加前缀', '添加后缀'])
        self.name_extra_edit = QLineEdit()
        name_layout.addWidget(self.name_rule_combo)
        name_layout.addWidget(self.name_extra_edit)
        eg_layout.addLayout(name_layout)

        # 输出格式 & JPEG 质量
        format_layout = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems(['保持原格式', 'JPEG', 'PNG'])
        self.jpeg_quality_slider = QSlider(Qt.Horizontal)
        self.jpeg_quality_slider.setRange(1, 100)
        self.jpeg_quality_slider.setValue(90)
        format_layout.addWidget(QLabel('格式'))
        format_layout.addWidget(self.format_combo)
        format_layout.addWidget(QLabel('JPEG质量'))
        format_layout.addWidget(self.jpeg_quality_slider)
        eg_layout.addLayout(format_layout)

        # 尺寸调整
        size_layout = QHBoxLayout()
        self.size_combo = QComboBox()
        self.size_combo.addItems(['不变', '按宽度', '按高度', '按百分比'])
        self.size_value = QSpinBox()
        self.size_value.setRange(1, 10000)
        self.size_value.setValue(100)
        size_layout.addWidget(QLabel('导出尺寸'))
        size_layout.addWidget(self.size_combo)
        size_layout.addWidget(self.size_value)
        eg_layout.addLayout(size_layout)

        self.btn_export = QPushButton('导出所选/全部图片')
        eg_layout.addWidget(self.btn_export)

        export_group.setLayout(eg_layout)
        left_col.addWidget(export_group, 4)

        main_layout.addLayout(left_col, 3)

        # ---------- 右侧: 上预览 下模板和水印设置 ----------
        right_col = QVBoxLayout()

        # 右上：预览
        preview_group = QGroupBox('图片预览（单击列表切换图片；可拖动水印）')
        pv_layout = QVBoxLayout()

        self.graphics_view = QGraphicsView()
        self.graphics_scene = QGraphicsScene()
        self.graphics_view.setScene(self.graphics_scene)
        pv_layout.addWidget(self.graphics_view)
        preview_group.setLayout(pv_layout)
        right_col.addWidget(preview_group, 7)

        # 右下：标签（文字/图片水印 + 模板）
        bottom_tabs = QTabWidget()
        bottom_tabs.setTabPosition(QTabWidget.North)

        # --- 水印设置页 ---
        watermark_tab = QWidget()
        wm_layout = QVBoxLayout()

        # 切换文本/图片
        self.watermark_type_combo = QComboBox()
        self.watermark_type_combo.addItems(['文本水印', '图片水印'])
        wm_layout.addWidget(self.watermark_type_combo)

        # 文本设置
        self.text_settings_widget = QWidget()
        ts_layout = QVBoxLayout()
        self.text_edit = QLineEdit('示例文字 — 水印')
        ts_layout.addWidget(QLabel('文本内容'))
        ts_layout.addWidget(self.text_edit)

        # 字体选择
        font_db = QFontDatabase()
        families = font_db.families()
        self.font_combo = QComboBox()
        self.font_combo.addItems(sorted(families))
        ts_layout.addWidget(QLabel('字体'))
        ts_layout.addWidget(self.font_combo)

        # 字号/样式
        style_layout = QHBoxLayout()
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 200)
        self.font_size_spin.setValue(36)
        self.chk_bold = QCheckBox('粗体')
        self.chk_italic = QCheckBox('斜体')
        style_layout.addWidget(QLabel('字号'))
        style_layout.addWidget(self.font_size_spin)
        style_layout.addWidget(self.chk_bold)
        style_layout.addWidget(self.chk_italic)
        ts_layout.addLayout(style_layout)

        # 颜色/透明度/旋转
        color_layout = QHBoxLayout()
        self.color_btn = QPushButton('选择颜色')
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(80)
        color_layout.addWidget(self.color_btn)
        color_layout.addWidget(QLabel('透明度'))
        color_layout.addWidget(self.opacity_slider)
        ts_layout.addLayout(color_layout)

        # 阴影/描边
        effect_layout = QHBoxLayout()
        self.chk_shadow = QCheckBox('阴影')
        self.chk_stroke = QCheckBox('描边')
        effect_layout.addWidget(self.chk_shadow)
        effect_layout.addWidget(self.chk_stroke)
        ts_layout.addLayout(effect_layout)

        # 旋转
        rotate_layout = QHBoxLayout()
        self.rotate_slider = QSlider(Qt.Horizontal)
        self.rotate_slider.setRange(-180, 180)
        self.rotate_slider.setValue(0)
        rotate_layout.addWidget(QLabel('旋转'))
        rotate_layout.addWidget(self.rotate_slider)
        ts_layout.addLayout(rotate_layout)

        # 预设位置（九宫格）
        pos_layout = QHBoxLayout()
        self.pos_combo = QComboBox()
        self.pos_combo.addItems(['左上', '上中', '右上', '左中', '居中', '右中', '左下', '下中', '右下'])
        pos_layout.addWidget(QLabel('预设位置'))
        pos_layout.addWidget(self.pos_combo)
        ts_layout.addLayout(pos_layout)

        # 缩放
        scale_layout = QHBoxLayout()
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(1, 1000)
        self.scale_spin.setValue(20)
        scale_layout.addWidget(QLabel('占比 (相对于图片宽度 %)'))
        scale_layout.addWidget(self.scale_spin)
        ts_layout.addLayout(scale_layout)

        self.text_settings_widget.setLayout(ts_layout)
        wm_layout.addWidget(self.text_settings_widget)

        # 图片水印设置
        self.image_settings_widget = QWidget()
        is_layout = QVBoxLayout()
        self.btn_choose_wm_image = QPushButton('选择 PNG 作为水印（支持透明）')
        self.wm_image_label = QLabel('未选择')
        is_layout.addWidget(self.btn_choose_wm_image)
        is_layout.addWidget(self.wm_image_label)

        # 图片透明度/旋转/缩放
        img_ctrl_layout = QHBoxLayout()
        self.img_opacity_slider = QSlider(Qt.Horizontal)
        self.img_opacity_slider.setRange(0, 100)
        self.img_opacity_slider.setValue(80)
        self.img_rotate_slider = QSlider(Qt.Horizontal)
        self.img_rotate_slider.setRange(-180, 180)
        self.img_rotate_slider.setValue(0)
        self.img_scale_spin = QSpinBox()
        self.img_scale_spin.setRange(1, 1000)
        self.img_scale_spin.setValue(20)
        img_ctrl_layout.addWidget(QLabel('透明度'))
        img_ctrl_layout.addWidget(self.img_opacity_slider)
        img_ctrl_layout.addWidget(QLabel('旋转'))
        img_ctrl_layout.addWidget(self.img_rotate_slider)
        img_ctrl_layout.addWidget(QLabel('占比%'))
        img_ctrl_layout.addWidget(self.img_scale_spin)
        is_layout.addLayout(img_ctrl_layout)

        # 图片位置预设
        img_pos_layout = QHBoxLayout()
        self.img_pos_combo = QComboBox()
        self.img_pos_combo.addItems(['左上', '上中', '右上', '左中', '居中', '右中', '左下', '下中', '右下'])
        img_pos_layout.addWidget(QLabel('预设位置'))
        img_pos_layout.addWidget(self.img_pos_combo)
        is_layout.addLayout(img_pos_layout)

        self.image_settings_widget.setLayout(is_layout)
        self.image_settings_widget.hide()
        wm_layout.addWidget(self.image_settings_widget)

        watermark_tab.setLayout(wm_layout)
        bottom_tabs.addTab(watermark_tab, '水印设置')

        # --- 模板管理页 ---
        templates_tab = QWidget()
        tpl_layout = QVBoxLayout()
        self.template_list = QListWidget()
        tpl_layout.addWidget(self.template_list)
        tpl_btn_layout = QHBoxLayout()
        self.btn_save_template = QPushButton('保存为模板')
        self.btn_load_template = QPushButton('加载模板')
        self.btn_delete_template = QPushButton('删除模板')
        tpl_btn_layout.addWidget(self.btn_save_template)
        tpl_btn_layout.addWidget(self.btn_load_template)
        tpl_btn_layout.addWidget(self.btn_delete_template)
        tpl_layout.addLayout(tpl_btn_layout)
        templates_tab.setLayout(tpl_layout)
        bottom_tabs.addTab(templates_tab, '模板管理')

        right_col.addWidget(bottom_tabs, 3)

        main_layout.addLayout(right_col, 7)

        # ---------- 事件绑定 ----------
        btn_add_files.clicked.connect(self.add_files)
        btn_add_folder.clicked.connect(self.add_folder)
        btn_clear.clicked.connect(self.clear_list)
        btn_choose_out.clicked.connect(self.choose_out_folder)
        self.list_widget.itemClicked.connect(self.on_list_item_clicked)
        self.btn_export.clicked.connect(self.export_images)

        # watermarks
        self.watermark_type_combo.currentIndexChanged.connect(self.on_watermark_type_changed)
        self.color_btn.clicked.connect(self.choose_color)
        self.font_combo.currentIndexChanged.connect(self.update_preview)
        self.font_size_spin.valueChanged.connect(self.update_preview)
        self.text_edit.textChanged.connect(self.update_preview)
        self.opacity_slider.valueChanged.connect(self.update_preview)
        self.rotate_slider.valueChanged.connect(self.update_preview)
        self.scale_spin.valueChanged.connect(self.update_preview)
        self.pos_combo.currentIndexChanged.connect(self.update_preview)
        self.chk_shadow.stateChanged.connect(self.update_preview)
        self.chk_stroke.stateChanged.connect(self.update_preview)
        self.chk_bold.stateChanged.connect(self.update_preview)
        self.chk_italic.stateChanged.connect(self.update_preview)

        self.btn_choose_wm_image.clicked.connect(self.choose_wm_image)
        self.img_opacity_slider.valueChanged.connect(self.update_preview)
        self.img_rotate_slider.valueChanged.connect(self.update_preview)
        self.img_scale_spin.valueChanged.connect(self.update_preview)
        self.img_pos_combo.currentIndexChanged.connect(self.update_preview)

        self.btn_save_template.clicked.connect(self.save_template)
        self.btn_load_template.clicked.connect(self.load_template)
        self.btn_delete_template.clicked.connect(self.delete_template)

        # 支持拖拽到 list
        # 使用自定义的 DragDropListWidget 并连接其 filesDropped 信号以添加图片路径
        self.list_widget.filesDropped.connect(self._add_image_paths)

        # populate templates list
        self._refresh_template_list()

        # default color
        self._color = QtGui.QColor(255, 255, 255)

    # ---------------- UI helpers ----------------
    def _drag_enter(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def _drop_event(self, event):
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls]
        files = []
        for p in paths:
            if os.path.isdir(p):
                # import folder
                for root, _, filenames in os.walk(p):
                    for fn in filenames:
                        if fn.lower().endswith(SUPPORTED_INPUT):
                            files.append(os.path.join(root, fn))
            elif os.path.isfile(p) and p.lower().endswith(SUPPORTED_INPUT):
                files.append(p)
        self._add_image_paths(files)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, '选择图片', '', 'Images (*.png *.jpg *.jpeg)')
        if files:
            self._add_image_paths(files)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择图片所在文件夹')
        if folder:
            files = []
            for root, _, filenames in os.walk(folder):
                for fn in filenames:
                    if fn.lower().endswith(SUPPORTED_INPUT):
                        files.append(os.path.join(root, fn))
            self._add_image_paths(files)

    def _add_image_paths(self, files):
        added = 0
        for f in files:
            if f not in self.images:
                self.images.append(f)
                # create thumbnail
                try:
                    im = Image.open(f)
                    im.thumbnail((240, 160))
                    pix = pil_image_to_qpixmap(im)
                    icon = QIcon(pix)
                except Exception:
                    icon = QIcon()
                item = QListWidgetItem(icon, os.path.basename(f))
                item.setData(Qt.UserRole, f)
                self.list_widget.addItem(item)
                added += 1
        if added > 0 and self.current_index is None:
            self.list_widget.setCurrentRow(0)
            self.on_list_item_clicked(self.list_widget.item(0))

    def clear_list(self):
        self.images = []
        self.list_widget.clear()
        self.graphics_scene.clear()
        self.current_index = None

    def choose_out_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择输出文件夹')
        if folder:
            self.out_folder_edit.setText(folder)

    def on_list_item_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path:
            try:
                self.current_index = self.images.index(path)
            except ValueError:
                self.current_index = None
            self.load_preview_image(path)

    def load_preview_image(self, path):
        self.graphics_scene.clear()
        try:
            im = Image.open(path).convert('RGBA')
            self.preview_base_image = im
            pix = pil_image_to_qpixmap(im)
            self.base_pixmap_item = QGraphicsPixmapItem(pix)
            self.graphics_scene.addItem(self.base_pixmap_item)
            # fit view
            self.graphics_view.fitInView(self.base_pixmap_item, Qt.KeepAspectRatio)
            # add watermark item
            self._add_preview_watermark()
        except Exception as e:
            QMessageBox.warning(self, '错误', f'无法打开图片：{e}')

    def _add_preview_watermark(self):
        # remove existing watermark items
        for it in list(self.graphics_scene.items()):
            if isinstance(it, (DraggableTextItem, DraggablePixmapItem)):
                self.graphics_scene.removeItem(it)

        if self.watermark_type_combo.currentText() == '文本水印':
            text = self.text_edit.text()
            ti = DraggableTextItem(text)
            font = QtGui.QFont(self.font_combo.currentText(), self.font_size_spin.value())
            font.setBold(self.chk_bold.isChecked())
            font.setItalic(self.chk_italic.isChecked())
            ti.setFont(font)
            ti.setDefaultTextColor(self._color)
            ti.setOpacity(self.opacity_slider.value() / 100.0)
            # position preset
            self._place_item_by_preset(ti, self.pos_combo.currentText())
            ti.set_rotation(self.rotate_slider.value())
            self.graphics_scene.addItem(ti)
            self.preview_watermark_item = ti
        else:
            # image watermark
            wm_path = getattr(self, 'wm_image_path', None)
            if wm_path and os.path.exists(wm_path):
                try:
                    wim = Image.open(wm_path).convert('RGBA')
                    # scale to percent of base width
                    base_w = self.preview_base_image.width
                    scale_percent = self.img_scale_spin.value()
                    target_w = max(1, int(base_w * scale_percent / 100.0))
                    wim.thumbnail((target_w, 10000), Image.ANTIALIAS)
                    pix = pil_image_to_qpixmap(wim)
                    pi = DraggablePixmapItem(pix)
                    pi.setOpacity(self.img_opacity_slider.value() / 100.0)
                    self._place_item_by_preset(pi, self.img_pos_combo.currentText())
                    pi.set_rotation(self.img_rotate_slider.value())
                    self.graphics_scene.addItem(pi)
                    self.preview_watermark_item = pi
                except Exception as e:
                    print('加载水印图失败', e)
            else:
                self.preview_watermark_item = None

    def _place_item_by_preset(self, item, preset_name):
        # 计算在 base_pixmap_item 上的位置
        if not hasattr(self, 'base_pixmap_item'):
            return
        base_rect = self.base_pixmap_item.boundingRect()
        it_rect = item.boundingRect()
        x = 0
        y = 0
        name = preset_name
        # horizontal
        if name in ('左上', '左中', '左下'):
            x = base_rect.left() + 10
        elif name in ('上中', '居中', '下中'):
            x = base_rect.left() + (base_rect.width() - it_rect.width()) / 2
        else:
            x = base_rect.right() - it_rect.width() - 10
        # vertical
        if name in ('左上', '上中', '右上'):
            y = base_rect.top() + 10
        elif name in ('左中', '居中', '右中'):
            y = base_rect.top() + (base_rect.height() - it_rect.height()) / 2
        else:
            y = base_rect.bottom() - it_rect.height() - 10
        item.setPos(QPointF(x, y))

    def on_watermark_type_changed(self, idx):
        if self.watermark_type_combo.currentText() == '文本水印':
            self.text_settings_widget.show()
            self.image_settings_widget.hide()
        else:
            self.text_settings_widget.hide()
            self.image_settings_widget.show()
        self._add_preview_watermark()

    def choose_color(self):
        col = QColorDialog.getColor(self._color, self, '选择字体颜色')
        if col.isValid():
            self._color = col
            self.update_preview()

    def choose_wm_image(self):
        f, _ = QFileDialog.getOpenFileName(self, '选择 PNG 图片作为水印', '', 'PNG 图片 (*.png)')
        if f:
            self.wm_image_path = f
            self.wm_image_label.setText(os.path.basename(f))
            self.update_preview()

    def update_preview(self):
        # refresh preview watermark item properties
        if not hasattr(self, 'preview_base_image'):
            return
        # rebuild to apply text/image changes
        self._add_preview_watermark()
        # fit view
        if hasattr(self, 'base_pixmap_item'):
            self.graphics_view.fitInView(self.base_pixmap_item, Qt.KeepAspectRatio)

    # ---------------- Template ----------------
    def _refresh_template_list(self):
        self.template_list.clear()
        for name in sorted(self.templates.keys()):
            it = QListWidgetItem(name)
            self.template_list.addItem(it)

    def save_template(self):
        name, ok = QtWidgets.QInputDialog.getText(self, '保存模板', '模板名称：')
        if not ok or not name.strip():
            return
        tpl = self._collect_settings()
        self.templates[name] = tpl
        save_json(TEMPLATES_FILE, self.templates)
        self._refresh_template_list()
        QMessageBox.information(self, '已保存', f'模板 {name} 已保存')

    def load_template(self):
        it = self.template_list.currentItem()
        if not it:
            QMessageBox.warning(self, '提示', '请先选择一个模板')
            return
        name = it.text()
        tpl = self.templates.get(name)
        if not tpl:
            return
        self._apply_settings(tpl)
        QMessageBox.information(self, '已加载', f'模板 {name} 已加载')

    def delete_template(self):
        it = self.template_list.currentItem()
        if not it:
            QMessageBox.warning(self, '提示', '请先选择一个模板')
            return
        name = it.text()
        if name in self.templates:
            del self.templates[name]
            save_json(TEMPLATES_FILE, self.templates)
            self._refresh_template_list()

    def _collect_settings(self):
        s = {
            'type': self.watermark_type_combo.currentText(),
            'text': self.text_edit.text(),
            'font': self.font_combo.currentText(),
            'font_size': self.font_size_spin.value(),
            'bold': self.chk_bold.isChecked(),
            'italic': self.chk_italic.isChecked(),
            'color': [self._color.red(), self._color.green(), self._color.blue(), self._color.alpha()],
            'opacity': self.opacity_slider.value(),
            'shadow': self.chk_shadow.isChecked(),
            'stroke': self.chk_stroke.isChecked(),
            'rotate': self.rotate_slider.value(),
            'pos': self.pos_combo.currentText(),
            'scale': self.scale_spin.value(),
            'wm_image': getattr(self, 'wm_image_path', ''),
            'img_opacity': self.img_opacity_slider.value(),
            'img_rotate': self.img_rotate_slider.value(),
            'img_scale': self.img_scale_spin.value(),
            'img_pos': self.img_pos_combo.currentText(),
        }
        return s

    def _apply_settings(self, s: dict):
        try:
            self.watermark_type_combo.setCurrentText(s.get('type', '文本水印'))
        except Exception:
            pass
        self.text_edit.setText(s.get('text', ''))
        if s.get('font'):
            try:
                self.font_combo.setCurrentText(s['font'])
            except Exception:
                pass
        self.font_size_spin.setValue(s.get('font_size', 36))
        self.chk_bold.setChecked(s.get('bold', False))
        self.chk_italic.setChecked(s.get('italic', False))
        col = s.get('color', [255, 255, 255, 255])
        self._color = QtGui.QColor(*col)
        self.opacity_slider.setValue(s.get('opacity', 80))
        self.chk_shadow.setChecked(s.get('shadow', False))
        self.chk_stroke.setChecked(s.get('stroke', False))
        self.rotate_slider.setValue(s.get('rotate', 0))
        self.pos_combo.setCurrentText(s.get('pos', '居中'))
        self.scale_spin.setValue(s.get('scale', 20))
        if s.get('wm_image'):
            self.wm_image_path = s.get('wm_image')
            self.wm_image_label.setText(os.path.basename(self.wm_image_path))
        self.img_opacity_slider.setValue(s.get('img_opacity', 80))
        self.img_rotate_slider.setValue(s.get('img_rotate', 0))
        self.img_scale_spin.setValue(s.get('img_scale', 20))
        self.img_pos_combo.setCurrentText(s.get('img_pos', '居中'))
        self.update_preview()

    # ---------------- Export ----------------
    def export_images(self):
        if not self.images:
            QMessageBox.warning(self, '提示', '没有要导出的图片')
            return
        out_folder = self.out_folder_edit.text().strip()
        if not out_folder:
            QMessageBox.warning(self, '提示', '请选择输出文件夹')
            return
        out_folder = os.path.abspath(out_folder)
        prevent = self.chk_prevent_overwrite.isChecked()
        if prevent:
            # check all images not in out_folder
            for p in self.images:
                if os.path.commonpath([out_folder, os.path.abspath(os.path.dirname(p))]) == out_folder:
                    QMessageBox.warning(self, '警告', '禁止导出到原文件夹，请选择其他输出文件夹或取消该选项')
                    return
        # export each image
        format_choice = self.format_combo.currentText()
        jpeg_quality = self.jpeg_quality_slider.value()
        resize_mode = self.size_combo.currentText()
        size_value = self.size_value.value()

        choose_all = QMessageBox.question(self, '导出', '是否导出全部图片？(否 = 只导出当前选中)',
                                          QMessageBox.Yes | QMessageBox.No)
        targets = []
        if choose_all == QMessageBox.Yes:
            targets = self.images[:]
        else:
            if self.current_index is None:
                QMessageBox.warning(self, '提示', '请先选择一张图片')
                return
            targets = [self.images[self.current_index]]

        total = len(targets)
        progress = QtWidgets.QProgressDialog('导出中...', '取消', 0, total, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        for idx, src in enumerate(targets):
            progress.setValue(idx)
            if progress.wasCanceled():
                break
            try:
                im = Image.open(src).convert('RGBA')
                out_im = self._apply_watermark_to_pil(im)
                # resize
                if resize_mode != '不变':
                    if resize_mode == '按宽度':
                        w = size_value
                        h = int(out_im.height * (w / out_im.width))
                        out_im = out_im.resize((w, h), Image.LANCZOS)
                    elif resize_mode == '按高度':
                        h = size_value
                        w = int(out_im.width * (h / out_im.height))
                        out_im = out_im.resize((w, h), Image.LANCZOS)
                    else:  # 百分比
                        p = size_value
                        w = int(out_im.width * p / 100.0)
                        h = int(out_im.height * p / 100.0)
                        out_im = out_im.resize((w, h), Image.LANCZOS)
                # naming
                base_name = os.path.splitext(os.path.basename(src))[0]
                ext = os.path.splitext(src)[1]
                rule = self.name_rule_combo.currentText()
                extra = self.name_extra_edit.text().strip()
                if rule == '保留原文件名':
                    name = base_name
                elif rule == '添加前缀':
                    name = f'{extra}{base_name}' if extra else f'wm_{base_name}'
                else:
                    name = f'{base_name}{extra}' if extra else f'{base_name}_watermarked'
                # format choice
                if format_choice == '保持原格式':
                    out_ext = ext.lower()
                elif format_choice == 'JPEG':
                    out_ext = '.jpg'
                else:
                    out_ext = '.png'
                out_path = os.path.join(out_folder, name + out_ext)
                # prevent overwrite
                if os.path.exists(out_path):
                    # add index
                    i = 1
                    while os.path.exists(os.path.join(out_folder, f'{name}_{i}{out_ext}')):
                        i += 1
                    out_path = os.path.join(out_folder, f'{name}_{i}{out_ext}')
                # save
                if out_ext in ('.jpg', '.jpeg'):
                    # convert to RGB
                    rgb = out_im.convert('RGB')
                    rgb.save(out_path, 'JPEG', quality=jpeg_quality)
                else:
                    out_im.save(out_path)
            except Exception as e:
                print('导出失败', src, e)
            QtWidgets.QApplication.processEvents()
        progress.setValue(total)
        QMessageBox.information(self, '完成', '导出操作已完成')

    def _apply_watermark_to_pil(self, base_im: Image.Image) -> Image.Image:
        """根据当前设置将水印绘制到 PIL 图像上并返回新的图像（RGBA）"""
        base = base_im.convert('RGBA')
        w, h = base.size
        overlay = Image.new('RGBA', base.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        if self.watermark_type_combo.currentText() == '文本水印':
            text = self.text_edit.text()
            font_family = self.font_combo.currentText()
            font_path = find_system_font_path(font_family) or None
            font_size = max(6, int(self.font_size_spin.value() * (w / 800.0)))
            try:
                if font_path:
                    pil_font = ImageFont.truetype(font_path, font_size)
                else:
                    pil_font = ImageFont.load_default()
            except Exception:
                pil_font = ImageFont.load_default()

            # measure
            left, top, right, bottom = pil_font.getbbox(text)
            tw, th = right - left, bottom - top
            # scale to requested percent
            target_w = int(w * (self.scale_spin.value() / 100.0))
            if tw > 0:
                scale_factor = target_w / tw
            else:
                scale_factor = 1.0
            font_size = max(6, int(font_size * scale_factor))
            try:
                if font_path:
                    pil_font = ImageFont.truetype(font_path, font_size)
                else:
                    pil_font = ImageFont.load_default()
            except Exception:
                pil_font = ImageFont.load_default()

            left, top, right, bottom = pil_font.getbbox(text)
            tw, th = right - left, bottom - top
            # color
            r, g, b, a = (self._color.red(), self._color.green(), self._color.blue(), 255)
            alpha = int(255 * (self.opacity_slider.value() / 100.0))
            fill = (r, g, b, alpha)

            # draw shadow/outline
            x, y = self._calc_position_for_pil(w, h, tw, th, self.pos_combo.currentText())
            if self.chk_shadow.isChecked():
                # draw shadow
                shadow_color = (0, 0, 0, int(alpha * 0.6))
                draw.text((x + 2, y + 2), text, font=pil_font, fill=shadow_color)
            if self.chk_stroke.isChecked():
                # stroke by drawing text multiple times around
                stroke_color = (0, 0, 0, alpha)
                offsets = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
                for ox, oy in offsets:
                    draw.text((x + ox, y + oy), text, font=pil_font, fill=stroke_color)
            draw.text((x, y), text, font=pil_font, fill=fill)
            # rotation
            rot = self.rotate_slider.value()
            if rot != 0:
                overlay = overlay.rotate(rot, expand=1)
                # composite onto base with centering
                base = Image.alpha_composite(base, Image.new('RGBA', base.size, (255, 255, 255, 0)))
                temp = Image.new('RGBA', base.size, (255, 255, 255, 0))
                tx = int((base.size[0] - overlay.size[0]) / 2)
                ty = int((base.size[1] - overlay.size[1]) / 2)
                temp.paste(overlay, (tx, ty), overlay)
                out = Image.alpha_composite(base, temp)
                return out
            else:
                out = Image.alpha_composite(base, overlay)
                return out

        else:
            # 图片水印
            wm_path = getattr(self, 'wm_image_path', None)
            if not wm_path or not os.path.exists(wm_path):
                return base
            try:
                wim = Image.open(wm_path).convert('RGBA')
                # scale to width percent
                target_w = max(1, int(w * (self.img_scale_spin.value() / 100.0)))
                ratio = target_w / wim.width
                new_size = (max(1, int(wim.width * ratio)), max(1, int(wim.height * ratio)))
                wim = wim.resize(new_size, Image.ANTIALIAS)
                # apply opacity
                alpha = int(255 * (self.img_opacity_slider.value() / 100.0))
                if alpha < 255:
                    a = wim.split()[3]
                    a = ImageEnhance.Brightness(a).enhance(alpha / 255.0)
                    wim.putalpha(a)
                # rotation
                rot = self.img_rotate_slider.value()
                if rot != 0:
                    wim = wim.rotate(rot, expand=1)
                # position
                tw, th = wim.size
                x, y = self._calc_position_for_pil(w, h, tw, th, self.img_pos_combo.currentText())
                overlay.paste(wim, (int(x), int(y)), wim)
                out = Image.alpha_composite(base, overlay)
                return out
            except Exception as e:
                print('图片水印应用失败', e)
                return base

    def _calc_position_for_pil(self, base_w, base_h, tw, th, preset):
        # 根据九宫格预设计算绘制坐标
        pad = 10
        if preset in ('左上', '左中', '左下'):
            x = pad
        elif preset in ('上中', '居中', '下中'):
            x = (base_w - tw) / 2
        else:
            x = base_w - tw - pad
        if preset in ('左上', '上中', '右上'):
            y = pad
        elif preset in ('左中', '居中', '右中'):
            y = (base_h - th) / 2
        else:
            y = base_h - th - pad
        return int(x), int(y)

    # ---------------- Last settings persistence ----------------
    def _load_last_settings(self):
        if self.last_settings:
            try:
                self._apply_settings(self.last_settings.get('watermark', {}))
                self.out_folder_edit.setText(self.last_settings.get('out_folder', ''))
                self.chk_prevent_overwrite.setChecked(self.last_settings.get('prevent_overwrite', True))
                # naming
                self.name_rule_combo.setCurrentText(self.last_settings.get('name_rule', '保留原文件名'))
                self.name_extra_edit.setText(self.last_settings.get('name_extra', ''))
            except Exception:
                pass

    def closeEvent(self, event):
        # save last settings
        s = {
            'watermark': self._collect_settings(),
            'out_folder': self.out_folder_edit.text(),
            'prevent_overwrite': self.chk_prevent_overwrite.isChecked(),
            'name_rule': self.name_rule_combo.currentText(),
            'name_extra': self.name_extra_edit.text(),
        }
        save_json(LAST_SETTINGS_FILE, s)
        event.accept()


# --------------------------- Run ---------------------------

def main():
    app = QApplication(sys.argv)
    win = WatermarkerApp()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
