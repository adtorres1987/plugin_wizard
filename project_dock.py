"""
Project Dock Widget
QDockWidget that manages projects before launching the Plugin Wizard.
Displays a QTreeWidget with the hierarchy:

  📁 PROJECT: {name}
      📁 {assessment}
          INPUT_{layer}          ← assessment_layer (all roles)
          📁 {analysis} (vN)
              OUTPUT_{result}    ← analysis_result
"""

from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QDialog,
    QDialogButtonBox, QSplitter, QMessageBox, QSizePolicy
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont

# Node-type tags stored in Qt.UserRole as (tag, primary_id, project_id)
_PROJECT    = 'project'
_ASSESSMENT = 'assessment'
_LAYER      = 'layer'
_ANALYSIS   = 'analysis'
_RESULT     = 'result'


class NewProjectDialog(QDialog):
    """Modal dialog for creating a new project."""

    def __init__(self, existing_names, parent=None):
        super().__init__(parent)
        self.existing_names = existing_names
        self.setWindowTitle("New Project")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Project name…")
        form.addRow("Name *", self.name_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setFixedHeight(80)
        self.desc_edit.setPlaceholderText("Optional description…")
        form.addRow("Description", self.desc_edit)

        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _validate_and_accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Project name is required.")
            return
        if name in self.existing_names:
            QMessageBox.warning(self, "Duplicate",
                                f"A project named '{name}' already exists.")
            return
        self.accept()

    def project_name(self):
        return self.name_edit.text().strip()

    def project_description(self):
        return self.desc_edit.toPlainText().strip() or None


class ProjectDockWidget(QDockWidget):
    """
    Dock panel shown in QGIS before the Plugin Wizard is launched.

    Signals:
        launch_wizard(int, str): emitted with (project_id, project_name)
                                 when the user clicks "New Assessment".
    """

    launch_wizard = pyqtSignal(int, str)

    def __init__(self, iface, project_db, parent=None):
        super().__init__("Assessment Projects", parent)
        self.iface = iface
        self.project_db = project_db
        self._selected_project_id = None

        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setMinimumWidth(300)

        self._build_ui()
        self._load_tree()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        splitter = QSplitter(Qt.Vertical)

        # ── Top: project tree ──────────────────────────────────────────
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel("Projects")
        bold = QFont()
        bold.setBold(True)
        lbl.setFont(bold)
        top_layout.addWidget(lbl)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        top_layout.addWidget(self.tree)

        splitter.addWidget(top)

        # ── Bottom: action buttons ──────────────────────────────────────
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)

        self.create_btn = QPushButton("+ New Project")
        self.create_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bottom_layout.addWidget(self.create_btn)

        self.selected_lbl = QLabel("No project selected")
        self.selected_lbl.setWordWrap(True)
        self.selected_lbl.setStyleSheet("color: grey; font-style: italic;")
        bottom_layout.addWidget(self.selected_lbl)

        self.launch_btn = QPushButton("New Assessment →")
        self.launch_btn.setEnabled(False)
        self.launch_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.launch_btn.setMinimumHeight(32)
        bottom_layout.addWidget(self.launch_btn)

        bottom_layout.addStretch()
        splitter.addWidget(bottom)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        root_layout.addWidget(splitter)
        self.setWidget(root)

        # Connections
        self.tree.itemSelectionChanged.connect(self._on_item_selected)
        self.create_btn.clicked.connect(self._create_project)
        self.launch_btn.clicked.connect(self._on_launch)

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def _load_tree(self):
        self.tree.clear()

        for proj in self.project_db.list_projects():
            proj_item = self._make_item(
                f"📁 PROJECT: {proj['name']}",
                (_PROJECT, proj['id'], proj['id']),
                tooltip=proj.get('description')
            )
            self.tree.addTopLevelItem(proj_item)
            self._populate_project(proj_item, proj['id'])
            proj_item.setExpanded(True)

        self.tree.resizeColumnToContents(0)

    def _populate_project(self, proj_item, project_id):
        for assessment in self.project_db.list_assessments(project_id):
            a_item = self._make_item(
                f"📁 {assessment['name']}",
                (_ASSESSMENT, assessment['id'], project_id),
                tooltip=assessment.get('description')
            )
            proj_item.addChild(a_item)
            self._populate_assessment(a_item, assessment['id'], project_id)

    def _populate_assessment(self, a_item, assessment_id, project_id):
        # Input layers — all assessment_layer records regardless of role
        for layer in self.project_db.list_assessment_layers(assessment_id):
            lyr_item = self._make_item(
                f"INPUT_{layer['layer_name']}",
                (_LAYER, layer['id'], project_id)
            )
            a_item.addChild(lyr_item)

        # Analyses and their output results
        for analysis in self.project_db.list_analyses(assessment_id):
            an_item = self._make_item(
                f"📁 {analysis['name']} (v{analysis['version_number']})",
                (_ANALYSIS, analysis['id'], project_id)
            )
            a_item.addChild(an_item)

            for result in self.project_db.list_analysis_results(analysis['id']):
                r_item = self._make_item(
                    f"OUTPUT_{result['name']}",
                    (_RESULT, result['id'], project_id)
                )
                an_item.addChild(r_item)

    @staticmethod
    def _make_item(text, data, tooltip=None):
        item = QTreeWidgetItem()
        item.setText(0, text)
        item.setData(0, Qt.UserRole, data)
        if tooltip:
            item.setToolTip(0, tooltip)
        return item

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_item_selected(self):
        items = self.tree.selectedItems()
        if not items:
            self._reset_selection()
            return

        item = items[0]
        data = item.data(0, Qt.UserRole)
        if not data:
            self._reset_selection()
            return

        node_type, primary_id, project_id = data
        self._selected_project_id = project_id

        if node_type == _PROJECT:
            proj = self.project_db.get_project(primary_id)
            assessments = self.project_db.list_assessments(primary_id)
            info = f"<b>{proj['name']}</b>"
            if proj.get('description'):
                info += f"<br><small>{proj['description']}</small>"
            info += f"<br><small>{len(assessments)} assessment(s)</small>"
            self.selected_lbl.setText(info)

        elif node_type == _ASSESSMENT:
            assessment = self.project_db.get_assessment(primary_id)
            analyses = self.project_db.list_analyses(primary_id)
            info = f"<b>{assessment['name']}</b>"
            if assessment.get('description'):
                info += f"<br><small>{assessment['description']}</small>"
            info += f"<br><small>{len(analyses)} analysis(es)</small>"
            self.selected_lbl.setText(info)

        else:
            self.selected_lbl.setText(f"<small>{item.text(0).strip()}</small>")

        self.selected_lbl.setStyleSheet("")
        self.launch_btn.setEnabled(True)

    def _reset_selection(self):
        self._selected_project_id = None
        self.selected_lbl.setText("No project selected")
        self.selected_lbl.setStyleSheet("color: grey; font-style: italic;")
        self.launch_btn.setEnabled(False)

    def _create_project(self):
        existing = [p['name'] for p in self.project_db.list_projects(active_only=False)]
        dlg = NewProjectDialog(existing, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return

        project_id = self.project_db.create_project(
            dlg.project_name(), dlg.project_description()
        )
        self._load_tree()

        # Auto-select the newly created project
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.UserRole)
            if data and data[0] == _PROJECT and data[1] == project_id:
                self.tree.setCurrentItem(item)
                break

    def _on_launch(self):
        if self._selected_project_id is None:
            return
        proj = self.project_db.get_project(self._selected_project_id)
        if proj:
            self.launch_wizard.emit(self._selected_project_id, proj['name'])

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def refresh(self):
        """Reload the tree; call after a wizard completes a new assessment."""
        current_project_id = self._selected_project_id
        self._load_tree()

        if current_project_id is not None:
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                data = item.data(0, Qt.UserRole)
                if data and data[0] == _PROJECT and data[1] == current_project_id:
                    self.tree.setCurrentItem(item)
                    break
