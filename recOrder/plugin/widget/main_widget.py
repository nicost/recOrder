from recOrder.calib.Calibration import QLIPP_Calibration
from pycromanager import Bridge
from PyQt5.QtCore import pyqtSlot, pyqtSignal, Qt
from PyQt5.QtWidgets import QWidget, QFileDialog, QSizePolicy, QSlider
from PyQt5.QtGui import QPixmap, QColor
from superqt import QDoubleRangeSlider, QRangeSlider
from recOrder.plugin.workers.calibration_workers import CalibrationWorker, BackgroundCaptureWorker, load_calibration
from recOrder.plugin.workers.acquisition_workers import PolarizationAcquisitionWorker, ListeningWorker, FluorescenceAcquisitionWorker
from recOrder.plugin.workers.reconstruction_workers import ReconstructionWorker
from recOrder.plugin.qtdesigner import recOrder_calibration_v5
from recOrder.postproc.post_processing import ret_ori_overlay, generic_hsv_overlay
from recOrder.io.core_functions import set_lc_state, snap_and_average
from recOrder.io.utils import load_bg
from waveorder.io.reader import WaveorderReader
from pathlib import Path, PurePath
from napari import Viewer
import numpy as np
import os
import json
import logging
from recOrder.io.config_reader import ConfigReader, PROCESSING, PREPROCESSING, POSTPROCESSING

#TODO:
# Parse the Microscope Parameters correctly
# Make the checks robust to every pipeline


class MainWidget(QWidget):

    # Initialize Signals
    mm_status_changed = pyqtSignal(bool)
    intensity_changed = pyqtSignal(float)
    log_changed = pyqtSignal(str)

    def __init__(self, napari_viewer: Viewer):
        super().__init__()
        self.viewer = napari_viewer

        # Setup GUI Elements
        self.ui = recOrder_calibration_v5.Ui_Form()
        self.ui.setupUi(self)
        self._promote_slider_init()

        # Setup Connections between elements
        # Connect to MicroManager
        self.ui.qbutton_mm_connect.clicked[bool].connect(self.connect_to_mm)

        # Calibration Tab
        self.ui.qbutton_browse.clicked[bool].connect(self.browse_dir_path)
        self.ui.le_directory.editingFinished.connect(self.enter_dir_path)
        self.ui.le_swing.editingFinished.connect(self.enter_swing)
        self.ui.le_wavelength.editingFinished.connect(self.enter_wavelength)
        self.ui.cb_calib_scheme.currentIndexChanged[int].connect(self.enter_calib_scheme)
        self.ui.cb_calib_mode.currentIndexChanged[int].connect(self.enter_calib_mode)
        self.ui.cb_lca.currentIndexChanged[int].connect(self.enter_dac_lca)
        self.ui.cb_lcb.currentIndexChanged[int].connect(self.enter_dac_lcb)
        self.ui.chb_use_roi.stateChanged[int].connect(self.enter_use_cropped_roi)
        self.ui.qbutton_calibrate.clicked[bool].connect(self.run_calibration)
        self.ui.qbutton_load_calib.clicked[bool].connect(self.load_calibration)
        self.ui.qbutton_calc_extinction.clicked[bool].connect(self.calc_extinction)
        self.ui.cb_config_group.currentIndexChanged[int].connect(self.enter_config_group)

        # Capture Background
        self.ui.le_bg_folder.editingFinished.connect(self.enter_bg_folder_name)
        self.ui.le_n_avg.editingFinished.connect(self.enter_n_avg)
        self.ui.qbutton_capture_bg.clicked[bool].connect(self.capture_bg)

        # Advanced
        self.ui.cb_loglevel.currentIndexChanged[int].connect(self.enter_log_level)
        self.ui.qbutton_push_note.clicked[bool].connect(self.push_note)

        # Acquisition Tab
        self.ui.qbutton_gui_mode.clicked[bool].connect(self.change_gui_mode)
        self.ui.qbutton_browse_save_dir.clicked[bool].connect(self.browse_save_path)
        self.ui.le_save_dir.editingFinished.connect(self.enter_save_path)
        self.ui.le_data_save_name.editingFinished.connect(self.enter_save_name)
        self.ui.qbutton_listen.clicked[bool].connect(self.listen_and_reconstruct)
        self.ui.le_zstart.editingFinished.connect(self.enter_zstart)
        self.ui.le_zend.editingFinished.connect(self.enter_zend)
        self.ui.le_zstep.editingFinished.connect(self.enter_zstep)
        self.ui.chb_use_gpu.stateChanged[int].connect(self.enter_use_gpu)
        self.ui.le_gpu_id.editingFinished.connect(self.enter_gpu_id)
        self.ui.le_obj_na.editingFinished.connect(self.enter_obj_na)
        self.ui.le_cond_na.editingFinished.connect(self.enter_cond_na)
        self.ui.le_mag.editingFinished.connect(self.enter_mag)
        self.ui.le_ps.editingFinished.connect(self.enter_ps)
        self.ui.le_n_media.editingFinished.connect(self.enter_n_media)
        self.ui.le_pad_z.editingFinished.connect(self.enter_pad_z)
        self.ui.chb_pause_updates.stateChanged[int].connect(self.enter_pause_updates)
        self.ui.cb_birefringence.currentIndexChanged[int].connect(self.enter_birefringence_dim)
        self.ui.cb_phase.currentIndexChanged[int].connect(self.enter_phase_dim)
        self.ui.cb_bg_method.currentIndexChanged[int].connect(self.enter_bg_correction)
        self.ui.le_bg_path.editingFinished.connect(self.enter_acq_bg_path)
        self.ui.qbutton_browse_bg_path.clicked[bool].connect(self.browse_acq_bg_path)
        self.ui.qbutton_acq_birefringence.clicked[bool].connect(self.acq_birefringence)
        self.ui.qbutton_acq_phase.clicked[bool].connect(self.acq_phase)
        self.ui.qbutton_acq_birefringence_phase.clicked[bool].connect(self.acq_birefringence_phase)
        self.ui.qbutton_acq_fluor.clicked[bool].connect(self.acquire_fluor_deconvolved)
        self.ui.cb_colormap.currentIndexChanged[int].connect(self.enter_colormap)
        self.ui.chb_display_volume.stateChanged[int].connect(self.enter_use_full_volume)
        self.ui.le_overlay_slice.editingFinished.connect(self.enter_display_slice)
        self.ui.slider_value.sliderMoved[tuple].connect(self.handle_val_slider_move)
        self.ui.slider_saturation.sliderMoved[tuple].connect(self.handle_sat_slider_move)

        # Display Tab
        self.viewer.layers.events.inserted.connect(self._add_layer_to_display_boxes)
        self.viewer.layers.events.removed.connect(self._remove_layer_from_display_boxes)
        self.ui.qbutton_create_overlay.clicked[bool].connect(self.create_overlay)
        self.ui.cb_saturation.currentIndexChanged[int].connect(self.update_sat_scale)
        self.ui.cb_value.currentIndexChanged[int].connect(self.update_value_scale)
        self.ui.le_sat_max.editingFinished.connect(self.enter_sat_max)
        self.ui.le_sat_min.editingFinished.connect(self.enter_sat_min)
        self.ui.le_val_max.editingFinished.connect(self.enter_val_max)
        self.ui.le_val_min.editingFinished.connect(self.enter_val_min)

        # Reconstruction
        self.ui.qbutton_browse_data_dir.clicked[bool].connect(self.browse_data_dir)
        self.ui.qbutton_browse_calib_meta.clicked[bool].connect(self.browse_calib_meta)
        self.ui.qbutton_load_config.clicked[bool].connect(self.load_config)
        self.ui.qbutton_save_config.clicked[bool].connect(self.save_config)
        self.ui.qbutton_load_default_config.clicked[bool].connect(self.load_default_config)
        self.ui.cb_method.currentIndexChanged[int].connect(self.enter_method)
        self.ui.cb_mode.currentIndexChanged[int].connect(self.enter_mode)
        self.ui.le_calibration_metadata.editingFinished.connect(self.enter_calib_meta)
        self.ui.qbutton_reconstruct.clicked[bool].connect(self.reconstruct)
        self.ui.cb_phase_denoiser.currentIndexChanged[int].connect(self.enter_phase_denoiser)

        # Logging
        log_box = QtLogger(self.ui.te_log)
        log_box.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
        logging.getLogger().addHandler(log_box)
        logging.getLogger().setLevel(logging.INFO)

        # Signal Emitters
        self.mm_status_changed.connect(self.handle_mm_status_update)

        # Instantiate Attributes:
        self.gui_mode = 'offline'
        self.mm = None
        self.mmc = None
        self.calib = None
        self.current_dir_path = str(Path.home())
        self.current_save_path = str(Path.home())
        self.current_bg_path = str(Path.home())
        self.directory = None

        # Reconstruction / Calibration Parameter Defaults
        self.swing = 0.1
        self.wavelength = 532
        self.calib_scheme = '4-State'
        self.calib_mode = 'retardance'
        self.config_group = 'Channel'
        self.last_calib_meta_file = None
        self.use_cropped_roi = False
        self.bg_folder_name = 'BG'
        self.n_avg = 5
        self.intensity_monitor = []
        self.save_directory = None
        self.save_name = None
        self.bg_option = 'None'
        self.birefringence_dim = '2D'
        self.phase_dim = '2D'
        self.z_start = None
        self.z_end = None
        self.z_step = None
        self.gpu_id = 0
        self.use_gpu = False
        self.obj_na = None
        self.cond_na = None
        self.mag = None
        self.ps = None
        self.n_media = 1.003
        self.pad_z = 0
        self.phase_reconstructor = None
        self.fluor_reconstructor = None
        self.acq_bg_directory = None
        self.auto_shutter = True
        self.lca_dac = None
        self.lcb_dac = None
        self.pause_updates = False
        self.method = 'QLIPP'
        self.mode = '3D'
        self.calib_path = str(Path.home())
        self.data_dir = str(Path.home())
        self.config_path = str(Path.home())
        self.save_config_path = str(Path.home())
        self.colormap = 'HSV'
        self.use_full_volume = False
        self.display_slice = 0
        self.last_p = 0
        self.reconstruction_data_path = None
        self.reconstruction_data = None

        # Assessment attributes
        self.calib_assessment_level = None

        # Init Plot
        self.plot_item = self.ui.plot_widget.getPlotItem()
        self.plot_item.enableAutoRange()
        self.plot_item.setLabel('left', 'Intensity')
        self.ui.plot_widget.setBackground((32, 34, 40))
        self.plot_sequence = 'Coarse'

        # Init thread worker
        self.worker = None

        # Display Images
        recorder_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        jch_legend_path = os.path.join(recorder_dir, 'docs/images/JCh_legend.png')
        hsv_legend_path = os.path.join(recorder_dir, 'docs/images/HSV_legend.png')
        self.jch_pixmap = QPixmap(jch_legend_path)
        self.hsv_pixmap = QPixmap(hsv_legend_path)
        self.ui.label_orientation_image.setPixmap(self.hsv_pixmap)
        logo_path = os.path.join(recorder_dir, 'docs/images/recOrder_plugin_logo.png')
        logo_pixmap = QPixmap(logo_path)
        self.ui.label_logo.setPixmap(logo_pixmap)

        # Hide initial UI elements for later implementation or for later pop-up purposes
        self.ui.label_lca.hide()
        self.ui.label_lcb.hide()
        self.ui.cb_lca.hide()
        self.ui.cb_lcb.hide()
        self._hide_acquisition_ui(True)
        self.ui.label_bg_path.setHidden(True)
        self.ui.le_bg_path.setHidden(True)
        self.ui.qbutton_browse_bg_path.setHidden(True)
        self.ui.le_rho.setHidden(True)
        self.ui.label_phase_rho.setHidden(True)
        self.ui.le_itr.setHidden(True)
        self.ui.label_itr.setHidden(True)

        # Set initial UI Properties
        self.ui.le_gui_mode.setStyleSheet("border: 1px solid rgb(200,0,0); color: rgb(200,0,0);")
        self.ui.te_log.setStyleSheet('background-color: rgb(32,34,40);')
        self.ui.le_mm_status.setText('Not Connected')
        self.ui.le_mm_status.setStyleSheet("border: 1px solid yellow;")
        self.ui.le_sat_min.setStyleSheet("background-color: rgba(0, 0, 0, 0);")
        self.ui.le_sat_max.setStyleSheet("background-color: rgba(0, 0, 0, 0);")
        self.ui.le_val_min.setStyleSheet("background-color: rgba(0, 0, 0, 0);")
        self.ui.le_val_max.setStyleSheet("background-color: rgba(0, 0, 0, 0);")
        self.setStyleSheet("QTabWidget::tab-bar {alignment: center;}")
        self.red_text = QColor(200, 0, 0, 255)
        self.original_tab_text = self.ui.tabWidget_3.tabBar().tabTextColor(0)
        self.ui.tabWidget.parent().setObjectName('recOrder')

        # group_boxes = ['recon_status', 'calib_params', 'run_calib', 'capture_background', 'acq_settings',
        #                'acquire', 'ReconSettings', 'phase', 'fluorescence', 'denoising', 'denoising_2', 'fluor',
        #                'registration', 'DisplayOptions']

        # for groupbox in group_boxes:
        #     box = getattr(self.ui, groupbox)
        #     # box.setStyleSheet("margin-top: -10ex;")
        #     box.setStyleSheet("QGroupBox::title {"
        #                       "subcontrol-origin: margin;"
        #                       "subcontrol-position: top left;")
                              # "min-height: 10ex;"
                              # "top: -1.5ex; }")

        # disable wheel events for combo boxes
        for attr_name in dir(self.ui):
            if 'cb_' in attr_name:
                attr = getattr(self.ui, attr_name)
                attr.wheelEvent = lambda event: None

        self.showMaximized()

    def _demote_slider_offline(self, ui_slider, range_):
        sizePolicy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)

        # Get Information from regular sliders
        slider_idx = self.ui.gridLayout_26.indexOf(ui_slider)
        slider_position = self.ui.gridLayout_26.getItemPosition(slider_idx)
        slider_parent = ui_slider.parent().objectName()
        slider_name = ui_slider.objectName()

        # Remove regular sliders from the UI
        self.ui.gridLayout_26.removeWidget(ui_slider)

        # Add back the sliders as range sliders with the same properties
        ui_slider = QSlider(getattr(self.ui, slider_parent))
        sizePolicy.setHeightForWidth(ui_slider.sizePolicy().hasHeightForWidth())
        ui_slider.setSizePolicy(sizePolicy)
        ui_slider.setOrientation(Qt.Horizontal)
        ui_slider.setObjectName(slider_name)
        self.ui.gridLayout_26.addWidget(ui_slider,
                                        slider_position[0],
                                        slider_position[1],
                                        slider_position[2],
                                        slider_position[3])
        ui_slider.setRange(range_[0], range_[1])

    def _promote_slider_offline(self, ui_slider, range_):

        sizePolicy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)

        # Get Information from regular sliders
        slider_idx = self.ui.gridLayout_26.indexOf(ui_slider)
        slider_position = self.ui.gridLayout_26.getItemPosition(slider_idx)
        slider_parent = ui_slider.parent().objectName()
        slider_name = ui_slider.objectName()
        print(slider_parent)

        # Remove regular sliders from the UI
        self.ui.gridLayout_26.removeWidget(ui_slider)

        # Add back the sliders as range sliders with the same properties
        ui_slider = QRangeSlider(getattr(self.ui, slider_parent))
        sizePolicy.setHeightForWidth(ui_slider.sizePolicy().hasHeightForWidth())
        ui_slider.setSizePolicy(sizePolicy)
        ui_slider.setOrientation(Qt.Horizontal)
        ui_slider.setObjectName(slider_name)
        self.ui.gridLayout_26.addWidget(ui_slider,
                                        slider_position[0],
                                        slider_position[1],
                                        slider_position[2],
                                        slider_position[3])
        ui_slider.setRange(range_[0], range_[1])

    def _promote_slider_init(self):

        sizePolicy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)

        # Get Information from regular sliders
        value_slider_idx = self.ui.gridLayout_17.indexOf(self.ui.slider_value)
        value_slider_position = self.ui.gridLayout_17.getItemPosition(value_slider_idx)
        value_slider_parent = self.ui.slider_value.parent().objectName()
        saturation_slider_idx = self.ui.gridLayout_17.indexOf(self.ui.slider_saturation)
        saturation_slider_position = self.ui.gridLayout_17.getItemPosition(saturation_slider_idx)
        saturation_slider_parent = self.ui.slider_saturation.parent().objectName()

        # Remove regular sliders from the UI
        self.ui.gridLayout_17.removeWidget(self.ui.slider_value)
        self.ui.gridLayout_17.removeWidget(self.ui.slider_saturation)

        # Add back the sliders as range sliders with the same properties
        self.ui.slider_saturation = QDoubleRangeSlider(getattr(self.ui, saturation_slider_parent))
        sizePolicy.setHeightForWidth(self.ui.slider_saturation.sizePolicy().hasHeightForWidth())
        self.ui.slider_saturation.setSizePolicy(sizePolicy)
        self.ui.slider_saturation.setOrientation(Qt.Horizontal)
        self.ui.slider_saturation.setObjectName("slider_saturation")
        self.ui.gridLayout_17.addWidget(self.ui.slider_saturation,
                                        saturation_slider_position[0],
                                        saturation_slider_position[1],
                                        saturation_slider_position[2],
                                        saturation_slider_position[3])
        self.ui.slider_saturation.setRange(0, 100)

        self.ui.slider_value = QDoubleRangeSlider(getattr(self.ui, value_slider_parent))
        sizePolicy.setHeightForWidth(self.ui.slider_value.sizePolicy().hasHeightForWidth())
        self.ui.slider_value.setSizePolicy(sizePolicy)
        self.ui.slider_value.setOrientation(Qt.Horizontal)
        self.ui.slider_value.setObjectName("slider_value")
        self.ui.gridLayout_17.addWidget(self.ui.slider_value,
                                        value_slider_position[0],
                                        value_slider_position[1],
                                        value_slider_position[2],
                                        value_slider_position[3])
        self.ui.slider_value.setRange(0, 100)

    def _hide_acquisition_ui(self, val: bool):
        self.ui.acq_settings.setHidden(val)
        self.ui.acquire.setHidden(val)

        # Calibration Tab
        self.ui.tabWidget.setTabEnabled(0, not val)
        if val:
            self.ui.tabWidget.setStyleSheet("QTabBar::tab::disabled {width: 0; height: 0; margin: 0; padding: 0; border: none;} ")
        else:
            self.ui.tabWidget.setStyleSheet("")
            self.ui.le_mm_status.setText('Not Connected')
            self.ui.le_mm_status.setStyleSheet("border: 1px solid yellow;")
            self.mmc = None
            self.mm = None
            self.ui.cb_config_group.clear()
            self.ui.tabWidget.setCurrentIndex(0)

    def _hide_offline_ui(self, val: bool):

        # General Settings
        self.ui.le_data_dir.setHidden(val)
        self.ui.label_data_dir.setHidden(val)
        self.ui.qbutton_browse_data_dir.setHidden(val)
        self.ui.le_calibration_metadata.setHidden(val)
        self.ui.label_calib_meta.setHidden(val)
        self.ui.qbutton_browse_calib_meta.setHidden(val)
        self.ui.qbutton_load_config.setHidden(val)
        self.ui.qbutton_save_config.setHidden(val)
        self.ui.qbutton_load_default_config.setHidden(val)
        self.ui.qbutton_reconstruct.setHidden(val)
        self.ui.qbutton_stop_reconstruct.setHidden(val)

        # Processing Settings
        self.ui.tabWidget_3.setTabEnabled(1, not val)
        if val:
            self.ui.tabWidget_3.setStyleSheet("QTabBar::tab::disabled {width: 0; height: 0; margin: 0; padding: 0; border: none;} ")
        else:
            self.ui.tabWidget_3.setStyleSheet("")

        # Pre/Post Processing
        self.ui.tabWidget_3.setTabEnabled(4, not val)
        if val:
            self.ui.tabWidget_3.setStyleSheet("QTabBar::tab::disabled {width: 0; height: 0; margin: 0; padding: 0; border: none;} ")
        else:
            self.ui.tabWidget_3.setStyleSheet("")

        self.ui.tabWidget_3.setTabEnabled(5, not val)
        if val:
            self.ui.tabWidget_3.setStyleSheet("QTabBar::tab::disabled {width: 0; height: 0; margin: 0; padding: 0; border: none;} ")
        else:
            self.ui.tabWidget_3.setStyleSheet("")

    def _enable_buttons(self):

        self.ui.qbutton_calibrate.setEnabled(True)
        self.ui.qbutton_capture_bg.setEnabled(True)
        self.ui.qbutton_calc_extinction.setEnabled(True)
        self.ui.qbutton_acq_birefringence.setEnabled(True)
        self.ui.qbutton_acq_phase.setEnabled(True)
        self.ui.qbutton_acq_birefringence_phase.setEnabled(True)
        self.ui.qbutton_acq_fluor.setEnabled(True)
        self.ui.qbutton_load_calib.setEnabled(True)
        self.ui.qbutton_listen.setEnabled(True)
        self.ui.qbutton_create_overlay.setEnabled(True)
        self.ui.qbutton_reconstruct.setEnabled(True)
        self.ui.qbutton_load_config.setEnabled(True)
        self.ui.qbutton_load_default_config.setEnabled(True)

    def _disable_buttons(self):
        self.ui.qbutton_calibrate.setEnabled(False)
        self.ui.qbutton_capture_bg.setEnabled(False)
        self.ui.qbutton_calc_extinction.setEnabled(False)
        self.ui.qbutton_acq_birefringence.setEnabled(False)
        self.ui.qbutton_acq_phase.setEnabled(False)
        self.ui.qbutton_acq_birefringence_phase.setEnabled(False)
        self.ui.qbutton_acq_fluor.setEnabled(False)
        self.ui.qbutton_load_calib.setEnabled(False)
        self.ui.qbutton_listen.setEnabled(False)
        self.ui.qbutton_create_overlay.setEnabled(False)
        self.ui.qbutton_reconstruct.setEnabled(False)
        self.ui.qbutton_load_config.setEnabled(False)
        self.ui.qbutton_load_default_config.setEnabled(False)

    def _handle_error(self, exc):
        self.ui.tb_calib_assessment.setText(f'Error: {str(exc)}')
        self.ui.tb_calib_assessment.setStyleSheet("border: 1px solid rgb(200,0,0);")

        if self.use_cropped_roi:
            self.mmc.clearROI()

        self.mmc.setAutoShutter(self.auto_shutter)
        self.ui.progress_bar.setValue(0)
        raise exc

    def _handle_calib_abort(self):
        if self.use_cropped_roi:
            self.mmc.clearROI()
        self.mmc.setAutoShutter(self.auto_shutter)
        self.ui.progress_bar.setValue(0)

    def _handle_acq_error(self, exc):
        raise exc

    def _handle_load_finished(self):
        self.ui.tb_calib_assessment.setText('Previous calibration successfully loaded')
        self.ui.tb_calib_assessment.setStyleSheet("border: 1px solid green;")
        self.ui.progress_bar.setValue(100)

    def _update_calib(self, val):
        self.calib = val

    def _add_layer_to_display_boxes(self, val):
        for layer in self.viewer.layers:
            if 'Overlay' in layer.name:
                continue
            if layer.name not in [self.ui.cb_hue.itemText(i) for i in range(self.ui.cb_hue.count())]:
                self.ui.cb_hue.addItem(layer.name)
            if layer.name not in [self.ui.cb_saturation.itemText(i) for i in range(self.ui.cb_saturation.count())]:
                self.ui.cb_saturation.addItem(layer.name)
            if layer.name not in [self.ui.cb_value.itemText(i) for i in range(self.ui.cb_value.count())]:
                self.ui.cb_value.addItem(layer.name)

    def _remove_layer_from_display_boxes(self, val):

        for i in range(self.ui.cb_hue.count()):
            if val.value.name in self.ui.cb_hue.itemText(i):
                self.ui.cb_hue.removeItem(i)
            if val.value.name in self.ui.cb_saturation.itemText(i):
                self.ui.cb_saturation.removeItem(i)
            if val.value.name in self.ui.cb_value.itemText(i):
                self.ui.cb_value.removeItem(i)

    def _set_tab_red(self, name, state):
        name_map = {'General': 0,
                    'Processing': 1,
                    'Physical': 2,
                    'Regularization': 3,
                    'preprocessing': 4,
                    'postprocessing': 5}

        index = name_map[name]

        if state:
            self.ui.tabWidget_3.tabBar().setTabTextColor(index, self.red_text)
        else:
            self.ui.tabWidget_3.tabBar().setTabTextColor(index, self.original_tab_text)

    def _check_line_edit(self, name):
        le = getattr(self.ui, f'le_{name}')
        text = le.text()

        if text == '':
            le.setStyleSheet("border: 1px solid rgb(200,0,0);")
            return False
        else:
            le.setStyleSheet("")
            return True

    def _check_requirements_for_acq(self, mode):
        self._set_tab_red('General', False)
        self._set_tab_red('Physical', False)
        self._set_tab_red('Processing', False)
        self._set_tab_red('Regularization', False)

        raise_error = False

        phase_required = {'wavelength', 'mag', 'cond_na', 'obj_na', 'n_media',
                          'phase_strength', 'ps', 'zstep'}

        fluor_required = {'recon_wavelength', 'mag', 'obj_na', 'n_media', 'fluor_strength', 'ps'}

        for field in phase_required:
            le = getattr(self.ui, f'le_{field}')
            le.setStyleSheet("")
        for field in fluor_required:
            le = getattr(self.ui, f'le_{field}')
            le.setStyleSheet("")

        if mode == 'birefringence' or mode == 'phase' or mode == 'fluor':
            success = self._check_line_edit('save_dir')
            if not success:
                raise_error = True
                self._set_tab_red('General', True)

            if self.bg_option == 'local_fit' or self.bg_option == 'Global':
                success = self._check_line_edit('bg_path')
                if not success:
                    raise_error = True
                    self._set_tab_red('General', True)

        if mode == 'phase':
            for field in phase_required:
                cont = self._check_line_edit(field)
                tab = getattr(self.ui, f'le_{field}').parent().parent().objectName()
                if not cont:
                    raise_error = True
                    if field != 'zstep':
                        self._set_tab_red(tab, True)
                else:
                    continue

        if mode == 'fluor':
            for field in fluor_required:
                cont = self._check_line_edit(field)
                tab = getattr(self.ui, f'le_{field}').parent().parent().objectName()
                if not cont:
                    raise_error = True
                    self._set_tab_red(tab, True)
                else:
                    continue
            if self.ui.chb_autocalc_bg.checkState() != 2:
                cont = self._check_line_edit('fluor_bg')
                tab = getattr(self.ui, f'le_fluor_bg').parent().parent().objectName()
                if not cont:
                    raise_error = True
                    self._set_tab_red(tab, True)

        if raise_error:
            raise ValueError('Please enter in all of the parameters necessary for the acquisition')

    def _check_requirements_for_reconstruction(self):
        self._set_tab_red('General', False)
        self._set_tab_red('Physical', False)
        self._set_tab_red('Processing', False)
        self._set_tab_red('Regularization', False)
        self._set_tab_red('preprocessing', False)
        self._set_tab_red('postprocessing', False)

        self.ui.qbutton_reconstruct.setStyleSheet("")

        success = True
        output_channels = self.ui.le_output_channels.text()

        always_required = {'data_dir', 'save_dir', 'positions', 'timepoints', 'output_channels'}
        birefringence_required = {'calibration_metadata', 'recon_wavelength'}
        phase_required = {'recon_wavelength', 'mag', 'obj_na', 'cond_na', 'n_media',
                          'phase_strength', 'ps'}
        fluor_decon_required = {'recon_wavelength', 'mag', 'obj_na', 'n_media', 'fluor_strength', 'ps'}

        for field in always_required:
            le = getattr(self.ui, f'le_{field}')
            le.setStyleSheet("")
        for field in phase_required:
            le = getattr(self.ui, f'le_{field}')
            le.setStyleSheet("")
        for field in fluor_decon_required:
            le = getattr(self.ui, f'le_{field}')
            le.setStyleSheet("")

        for field in always_required:
            cont = self._check_line_edit(field)
            if not cont:
                success = False
                if field == 'data_dir' or field == 'save_dir':
                    self._set_tab_red('General', True)
                if field == 'positions' or field == 'timepoints' or field == 'output_channels':
                    self._set_tab_red('Processing', True)
            else:
                continue

        if self.method == 'QLIPP':
            if 'Retardance' in output_channels or 'Orientation' in output_channels or 'BF' in output_channels:
                for field in birefringence_required:
                    cont = self._check_line_edit(field)
                    tab = getattr(self.ui, f'le_{field}').parent().parent().objectName()
                    if not cont:
                        if field != 'calibration_metadata':
                            self._set_tab_red(tab, True)
                        success = False
                    else:
                        self._set_tab_red(tab, False)
                        continue

            elif 'Phase2D' in output_channels or 'Phase3D' in output_channels:
                for field in phase_required:
                    cont = self._check_line_edit(field)
                    tab = getattr(self.ui, f'le_{field}').parent().parent().objectName()
                    if not cont:
                        self._set_tab_red(tab, True)
                        success = False
                    else:
                        self._set_tab_red(tab, False)
                        continue
                # if 'Phase2D' in output_channels:
                #     cont = self._check_line_edit('focus_zidx')
                #     if not cont:
                #         self._set_tab_red('Physical', True)
                #         success = False
                #     else:
                #         self._set_tab_red('Physical', False)

            else:
                self._set_tab_red('Processing', True)
                self.ui.le_output_channels.setStyleSheet("border: 1px solid rgb(200,0,0);")
                print('User did not specify any QLIPP Specific Channels')
                success = False

        elif self.method == 'PhaseFromBF':
            cont = self._check_line_edit('fluor_chan')
            tab = getattr(self.ui, f'le_fluor_chan').parent().parent().objectName()
            if not cont:
                self._set_tab_red(tab, True)
                success = False

            if 'Phase2D' in output_channels or 'Phase3D' in output_channels:
                for field in phase_required:
                    cont = self._check_line_edit(field)
                    tab = getattr(self.ui, f'le_{field}').parent().parent().objectName()
                    if not cont:
                        self._set_tab_red(tab, True)
                        success = False
                    else:
                        self._set_tab_red(tab, False)
                        continue
                # if 'Phase2D' in output_channels:
                #     cont = self._check_line_edit('focus_zidx')
                #     if not cont:
                #         self._set_tab_red('Physical', True)
                #         success = False
                #     else:
                #         self._set_tab_red('Physical', False)
            else:
                self._set_tab_red('Processing', True)
                self.ui.le_output_channels.setStyleSheet("border: 1px solid rgb(200,0,0);")
                print('User did not specify any PhaseFromBF Specific Channels (Phase2D, Phase3D)')
                success = False

        elif self.method == 'FluorDeconv':
            cont = self._check_line_edit('fluor_chan')
            tab = getattr(self.ui, f'le_fluor_chan').parent().parent().objectName()
            if not cont:
                self._set_tab_red(tab, True)
                success = False

            for field in fluor_decon_required:
                cont = self._check_line_edit(field)
                tab = getattr(self.ui, f'le_{field}').parent().parent().objectName()
                if not cont:
                    self._set_tab_red(tab, True)
                    success = False
                else:
                    self._set_tab_red(tab, False)
                    continue

        else:
            print('Error in parameter checks')
            self.ui.qbutton_reconstruct.setStyleSheet("border: 1px solid rgb(200,0,0);")
            success = False

        return success

    def _populate_config_from_app(self):
        self.config_reader = ConfigReader(immutable=False)

        # Parse dataset fields manually
        self.config_reader.data_dir = self.ui.le_data_dir.text()
        self.data_dir = self.ui.le_data_dir.text()
        self.config_reader.save_dir = self.ui.le_save_dir.text()
        self.save_directory = self.ui.le_save_dir.text()
        self.config_reader.method = self.method
        self.config_reader.mode = self.mode
        self.config_reader.data_save_name = self.ui.le_data_save_name.text() if self.ui.le_data_save_name.text() != '' else None
        self.config_reader.calibration_metadata = self.ui.le_calibration_metadata.text()
        self.config_reader.background = self.ui.le_bg_path.text()
        self.config_reader.background_correction = self.bg_option

        # Assumes that positions/timepoints can either be 'all'; '[all]'; 1, 2, 3, N; [start, end]
        positions = self.ui.le_positions.text()
        positions = positions.replace(' ', '')
        if positions == 'all' or positions == "['all']" or positions == '[all]':
            self.config_reader.positions = ['all']
        elif positions.startswith('[') and positions.endswith(']'):
            vals = positions[1:-1].split(',')
            if len(vals) != 2:
                self._set_tab_red('Processing', True)
                self.ui.le_positions.setStyleSheet("border: 1px solid rgb(200,0,0);")
            else:
                self._set_tab_red('Processing', False)
                self.ui.le_positions.setStyleSheet("")
                self.config_reader.positions = [(int(vals[0]), int(vals[1]))]
        else:
            vals = positions.split(',')
            vals = map(lambda x: int(x), vals)
            self.config_reader.positions = list(vals)

        timepoints = self.ui.le_timepoints.text()
        timepoints = timepoints.replace(' ', '')
        if timepoints == 'all' or timepoints == "['all']" or timepoints == '[all]':
            self.config_reader.timepoints = ['all']
        elif timepoints.startswith('[') and timepoints.endswith(']'):
            vals = timepoints[1:-1].split(',')
            if len(vals) != 2:
                self._set_tab_red('Processing', True)
                self.ui.le_timepoints.setStyleSheet("border: 1px solid rgb(200,0,0);")
            else:
                self._set_tab_red('Processing', False)
                self.ui.le_timepoints.setStyleSheet("")
                self.config_reader.timepoints = [(int(vals[0]), int(vals[1]))]
        else:
            vals = timepoints.split(',')
            vals = map(lambda x: int(x), vals)
            self.config_reader.timepoints = list(vals)

        for key, value in PREPROCESSING.items():
            if isinstance(value, dict):
                for key_child, value_child in PREPROCESSING[key].items():
                    if key_child == 'use':
                        field = getattr(self.ui, f'chb_preproc_{key}_{key_child}')
                        val = True if field.checkState() == 2 else False
                        setattr(self.config_reader.preprocessing, f'{key}_{key_child}', val)
                    else:
                        field = getattr(self.ui, f'le_preproc_{key}_{key_child}')
                        setattr(self.config_reader.preprocessing, f'{key}_{key_child}', field.text())
            else:
                setattr(self.config_reader.preprocessing, key, getattr(self, key))

        attrs = dir(self.ui)
        skip = ['wavelength', 'pixel_size', 'magnification', 'NA_objective', 'NA_condenser', 'n_objective_media']
        # TODO: Figure out how to catch errors in regularizer strength field
        for key, value in PROCESSING.items():
            if key not in skip:
                if key == 'background_correction':
                    bg_map = {0: 'None', 1: 'global', 2: 'local_fit'}
                    setattr(self.config_reader, key, bg_map[self.ui.cb_bg_method.currentIndex()])

                elif key == 'output_channels':
                    field_text = self.ui.le_output_channels.text()
                    channels = field_text.split(',')
                    channels = [i.replace(' ', '') for i in channels]
                    setattr(self.config_reader, key, channels)

                elif key == 'pad_z':
                    val = self.ui.le_pad_z.text()
                    setattr(self.config_reader, key, int(val))
                else:
                    attr_name = f'le_{key}'
                    if attr_name in attrs:
                        le = getattr(self.ui, attr_name)
                        try:
                            setattr(self.config_reader, key, float(le.text()))
                        except ValueError as err:
                            print(err)
                            tab = le.parent().parent().objectName()
                            self._set_tab_red(tab, True)
                            le.setStyleSheet("border: 1px solid rgb(200,0,0);")
                    else:
                        continue

        # Parse name mismatch fields
        setattr(self.config_reader, 'wavelength', int(self.ui.le_recon_wavelength.text()))
        setattr(self.config_reader, 'NA_objective', float(self.ui.le_obj_na.text()))
        setattr(self.config_reader, 'NA_condenser', float(self.ui.le_cond_na.text()))
        setattr(self.config_reader, 'pixel_size', float(self.ui.le_ps.text()))
        setattr(self.config_reader, 'n_objective_media', float(self.ui.le_n_media.text()))
        setattr(self.config_reader, 'magnification', float(self.ui.le_mag.text()))

        if self.method == 'FluorDeconv':
            fluor_chan = self.ui.le_fluor_chan.text()
            channels = fluor_chan.split(',')
            channels = [int(i.replace(' ', '')) for i in channels]
            setattr(self.config_reader, 'fluorescence_channel_indices', channels)

        if self.method == 'PhaseFromBF':
            setattr(self.config_reader, 'brightfield_channel_index',
                      int(self.ui.le_fluor_chan.text()))

        # Parse Postprocessing automatically
        for key, val in POSTPROCESSING.items():
            for key_child, val_child in val.items():
                if key == 'deconvolution':
                    if key_child == 'use':
                        cb = getattr(self.ui, f'chb_postproc_fluor_{key_child}')
                        val = True if cb.checkState() == 2 else False
                        setattr(self.config_reader.postprocessing, f'deconvolution_{key_child}', val)
                    if hasattr(self.ui, f'le_postproc_fluor_{key_child}'):
                        # TODO: Parse wavelengths and channel indices in a smart manor
                        le = getattr(self.ui, f'le_postproc_fluor_{key_child}')
                        setattr(self.config_reader.postprocessing, f'deconvolution_{key_child}', le.text())

                elif key == 'registration':
                    if key_child == 'use':
                        cb = getattr(self.ui, 'chb_postproc_reg_use')
                        val = True if cb.checkState() == 2 else False
                        setattr(self.config_reader.postprocessing, f'{key}_{key_child}', val)
                    else:
                        le = getattr(self.ui, f'le_postproc_reg_{key_child}')
                        setattr(self.config_reader.postprocessing, f'{key}_{key_child}', le.text())

                elif key == 'denoise':
                    if key_child == 'use':
                        cb = getattr(self.ui, 'chb_postproc_denoise_use')
                        val = True if cb.checkState() == 2 else False
                        setattr(self.config_reader.postprocessing, f'{key}_{key_child}', val)
                    else:
                        le = getattr(self.ui, f'le_postproc_denoise_{key_child}')
                        setattr(self.config_reader.postprocessing, f'{key}_{key_child}', le.text())

    def _populate_from_config(self):
        # Parse dataset fields manually
        self.data_dir = self.config_reader.data_dir

        self.ui.le_data_dir.setText(self.config_reader.data_dir)
        self.save_directory = self.config_reader.save_dir
        self.ui.le_save_dir.setText(self.config_reader.save_dir)
        self.ui.le_data_save_name.setText(self.config_reader.data_save_name)
        self.ui.le_calibration_metadata.setText(self.config_reader.calibration_metadata)
        self.ui.le_bg_path.setText(self.config_reader.background)

        self.mode = self.config_reader.mode
        self.ui.cb_mode.setCurrentIndex(0) if self.mode == '3D' else self.ui.cb_mode.setCurrentIndex(1)
        self.method = self.config_reader.method
        if self.method == 'QLIPP':
            self.ui.cb_method.setCurrentIndex(0)
        elif self.method == 'PhaseFromBF':
            self.ui.cb_method.setCurrentIndex(1)
        elif self.method == 'FluorDeconv':
            self.ui.cb_method.setCurrentIndex(2)
        else:
            print(f'Did not understand method from config: {self.method}')
            self.ui.cb_method.setStyleSheet("border: 1px solid rgb(200,0,0);")

        self.bg_option = self.config_reader.background_correction
        if self.bg_option == 'None':
            self.ui.cb_bg_method.setCurrentIndex(0)
        elif self.bg_option == 'Global':
            self.ui.cb_bg_method.setCurrentIndex(1)
        elif self.bg_option == 'local_fit':
            self.ui.cb_bg_method.setCurrentIndex(2)
        else:
            print(f'Did not understand method from config: {self.method}')
            self.ui.cb_method.setStyleSheet("border: 1px solid rgb(200,0,0);")

        self.ui.le_positions.setText(str(self.config_reader.positions))
        self.ui.le_timepoints.setText(str(self.config_reader.timepoints))

        # Parse Preprocessing automatically
        for key, val in PREPROCESSING.items():
            for key_child, val_child in val.items():
                if key_child == 'use':
                    attr = getattr(self.config_reader.preprocessing, 'denoise_use')
                    self.ui.chb_preproc_denoise_use.setCheckState(attr)
                else:
                    le = getattr(self.ui, f'le_preproc_denoise_{key_child}')
                    le.setText(str(getattr(self.config_reader.preprocessing, f'denoise_{key_child}')))

        # Parse Processing name mismatch fields
        self.ui.le_recon_wavelength.setText(str(int(self.config_reader.wavelength)))
        self.ui.le_obj_na.setText(str(self.config_reader.NA_objective))
        self.ui.le_cond_na.setText(str(self.config_reader.NA_condenser))
        self.ui.le_ps.setText(str(self.config_reader.pixel_size))
        self.ui.le_n_media.setText(str(self.config_reader.n_objective_media))
        self.ui.le_mag.setText(str(self.config_reader.magnification))

        # Parse processing automatically
        denoiser = None
        for key, val in PROCESSING.items():
            if key == 'output_channels':
                channels = self.config_reader.output_channels
                text = ''
                for idx, chan in enumerate(channels):
                    text += f'{chan}, ' if idx != len(channels)-1 else f'{chan}'

                self.ui.le_output_channels.setText(text)

            elif key == 'use_gpu':
                state = getattr(self.config_reader, key)
                self.ui.chb_use_gpu.setChecked(state)

            elif key == 'gpu_id':
                val = str(int(getattr(self.config_reader, key)))
                self.ui.le_gpu_id.setText(val)

            elif key == 'pad_z':
                val = str(int(getattr(self.config_reader, key)))
                self.ui.le_pad_z.setText(val)

            elif hasattr(self.ui, f'le_{key}'):
                le = getattr(self.ui, f'le_{key}')
                le.setText(str(getattr(self.config_reader, key)) if not isinstance(getattr(self.config_reader, key),
                                                                                   str) else getattr(self.config_reader,
                                                                                                     key))
            elif hasattr(self.ui, f'cb_{key}'):
                cb = getattr(self.ui, f'cb_{key}')
                items = [cb.itemText(i) for i in range(cb.count())]
                cfg_attr = getattr(self.config_reader, key)
                self.ui.cb_mode.setCurrentIndex(items.index(cfg_attr))

            elif key == 'phase_denoiser_2D' or key == 'phase_denoiser_3D':
                cb = self.ui.cb_phase_denoiser
                cfg_attr = getattr(self.config_reader, f'phase_denoiser_{self.mode}')
                denoiser = cfg_attr
                cb.setCurrentIndex(0) if cfg_attr == 'Tikhonov' else cb.setCurrentIndex(1)
            else:
                if denoiser == 'Tikhonov':
                    strength = getattr(self.config_reader, f'Tik_reg_ph_{self.mode}')
                    self.ui.le_phase_strength.setText(str(strength))
                else:
                    strength = getattr(self.config_reader, f'TV_reg_ph_{self.mode}')
                    self.ui.le_phase_strength.setText(str(strength))
                    self.ui.le_rho.setText(str(getattr(self.config_reader, f'rho_{self.mode}')))
                    self.ui.le_itr.setText(str(getattr(self.config_reader, f'itr_{self.mode}')))

        # Parse Postprocessing automatically
        for key, val in POSTPROCESSING.items():
            for key_child, val_child in val.items():
                if key == 'deconvolution':
                    if key_child == 'use':
                        attr = getattr(self.config_reader.postprocessing, 'registration_use')
                        self.ui.chb_preproc_denoise_use.setCheckState(attr)
                    if hasattr(self.ui, f'le_postproc_fluor_{key_child}'):
                        le = getattr(self.ui, f'le_postproc_fluor_{key_child}')
                        attr = str(getattr(self.config_reader.postprocessing, f'{key}_{key_child}'))
                        le.setText(attr)

                elif key == 'registration':
                    if key_child == 'use':
                        attr = getattr(self.config_reader.postprocessing, 'registration_use')
                        self.ui.chb_postproc_reg_use.setCheckState(attr)
                    else:
                        le = getattr(self.ui, f'le_postproc_reg_{key_child}')
                        attr = str(getattr(self.config_reader.postprocessing, f'registration_{key_child}'))
                        le.setText(attr)

                elif key == 'denoise':
                    if key_child == 'use':
                        attr = getattr(self.config_reader.postprocessing, 'denoise_use')
                        self.ui.chb_postproc_denoise_use.setCheckState(attr)
                    else:
                        le = getattr(self.ui, f'le_postproc_denoise_{key_child}')
                        attr = str(getattr(self.config_reader.postprocessing, f'denoise_{key_child}'))
                        le.setText(attr)

    @pyqtSlot(bool)
    def change_gui_mode(self):
        if self.gui_mode == 'offline':
            self.ui.qbutton_gui_mode.setText('Switch to Offline')
            self.ui.le_gui_mode.setText('Online')
            self.ui.le_gui_mode.setStyleSheet("border: 1px solid green; color: green;")
            self._hide_offline_ui(True)
            self._hide_acquisition_ui(False)
            self.gui_mode = 'online'
        else:
            self.ui.qbutton_gui_mode.setText('Switch to Online')
            self.ui.le_gui_mode.setText('Offline')
            self.ui.le_gui_mode.setStyleSheet("border: 1px solid rgb(200,0,0); color: rgb(200,0,0);")
            self._hide_offline_ui(False)
            self._hide_acquisition_ui(True)
            self.gui_mode = 'offline'

    @pyqtSlot(bool)
    def connect_to_mm(self):
        try:
            bridge = Bridge(convert_camel_case=False)
            self.mmc = bridge.get_core()
            self.mm = bridge.get_studio()
            self.ui.cb_config_group.clear()
            groups = self.mmc.getAvailableConfigGroups()
            group_list = []
            for i in range(groups.size()):
                group_list.append(groups.get(i))
            self.ui.cb_config_group.addItems(group_list)
            self.mm_status_changed.emit(True)
        except:
            self.mm_status_changed.emit(False)

    @pyqtSlot(bool)
    def handle_mm_status_update(self, value):
        if value:
            self.ui.le_mm_status.setText('Sucess!')
            self.ui.le_mm_status.setStyleSheet("background-color: green;")

        else:
            self.ui.le_mm_status.setText('Failed.')
            self.ui.le_mm_status.setStyleSheet("background-color: rgb(200,0,0);")

    @pyqtSlot(tuple)
    def handle_progress_update(self, value):
        self.ui.progress_bar.setValue(value[0])
        self.ui.label_progress.setText('Progress: ' + value[1])

    @pyqtSlot(str)
    def handle_extinction_update(self, value):
        self.ui.le_extinction.setText(value)

    @pyqtSlot(object)
    def handle_plot_update(self, value):
        self.intensity_monitor.append(value)
        self.ui.plot_widget.plot(self.intensity_monitor)

        if self.plot_sequence[0] == 'Coarse':
            self.plot_item.autoRange()
        else:
            self.plot_item.setRange(xRange=(self.plot_sequence[1], len(self.intensity_monitor)),
                                    yRange=(0, np.max(self.intensity_monitor[self.plot_sequence[1]:])),
                                    padding=0.1)

    @pyqtSlot(str)
    def handle_calibration_assessment_update(self, value):
        self.calib_assessment_level = value

    @pyqtSlot(str)
    def handle_calibration_assessment_msg_update(self, value):
        self.ui.tb_calib_assessment.setText(value)

        if self.calib_assessment_level == 'good':
            self.ui.tb_calib_assessment.setStyleSheet("border: 1px solid green;")
        elif self.calib_assessment_level == 'okay':
            self.ui.tb_calib_assessment.setStyleSheet("border: 1px solid rgb(252,190,3);")
        elif self.calib_assessment_level == 'bad':
            self.ui.tb_calib_assessment.setStyleSheet("border: 1px solid rgb(200,0,0);")
        else:
            pass

    @pyqtSlot(object)
    def handle_bg_image_update(self, value):

        if 'Background Images' in self.viewer.layers:
            self.viewer.layers['Background Images'].data = value
        else:
            self.viewer.add_image(value, name='Background Images', colormap='gray')

    @pyqtSlot(object)
    def handle_bg_bire_image_update(self, value):

        # Separate Background Retardance and Background Orientation
        # Add new layer if none exists, otherwise update layer data
        if 'Background Retardance' in self.viewer.layers:
            self.viewer.layers['Background Retardance'].data = value[0]
        else:
            self.viewer.add_image(value[0], name='Background Retardance', colormap='gray')

        if 'Background Orientation' in self.viewer.layers:
            self.viewer.layers['Background Orientation'].data = value[1]
        else:
            self.viewer.add_image(value[1], name='Background Orientation', colormap='gray')

    @pyqtSlot(object)
    def handle_bire_image_update(self, value):

        channel_names = {'Orientation': 1,
                         'Retardance': 0,
                         }

        # Compute Overlay if birefringence acquisition is 2D
        if self.birefringence_dim == '2D':
            channel_names['BirefringenceOverlay'] = None
            overlay = ret_ori_overlay(retardance=value[0],
                                      orientation=value[1],
                                      scale=(0, np.percentile(value[0], 99.99)),
                                      cmap=self.colormap)

        for key, chan in channel_names.items():
            if key == 'BirefringenceOverlay':
                if key+self.birefringence_dim in self.viewer.layers:
                    self.viewer.layers[key+self.birefringence_dim].data = overlay
                else:
                    self.viewer.add_image(overlay, name=key+self.birefringence_dim, rgb=True)
            else:
                if key+self.birefringence_dim in self.viewer.layers:
                    self.viewer.layers[key+self.birefringence_dim].data = value[chan]
                else:
                    cmap = 'gray' if key != 'Orientation' else 'hsv'
                    self.viewer.add_image(value[chan], name=key+self.birefringence_dim, colormap=cmap)

        # if self.ui.DisplayOptions.isHidden():
        #     self.ui.DisplayOptions.show()

    @pyqtSlot(object)
    def handle_phase_image_update(self, value):

        name = 'Phase2D' if self.phase_dim == '2D' else 'Phase3D'

        # Add new layer if none exists, otherwise update layer data
        if name in self.viewer.layers:
            self.viewer.layers[name].data = value
        else:
            self.viewer.add_image(value, name=name, colormap='gray')

        if 'Phase' not in [self.ui.cb_saturation.itemText(i) for i in range(self.ui.cb_saturation.count())]:
            self.ui.cb_saturation.addItem('Retardance')
        if 'Phase' not in [self.ui.cb_value.itemText(i) for i in range(self.ui.cb_value.count())]:
            self.ui.cb_value.addItem('Retardance')

    @pyqtSlot(object)
    def handle_fluor_image_update(self, value):

        mode = '2D' if self.ui.cb_fluor_dim.currentIndex() == 0 else '3D'
        name = f'FluorDeconvolved{mode}'

        # Add new layer if none exists, otherwise update layer data
        if name in self.viewer.layers:
            self.viewer.layers[name].data = value
        else:
            self.viewer.add_image(value, name=name, colormap='gray')

    @pyqtSlot(object)
    def handle_qlipp_reconstructor_update(self, value):
        # Saves phase reconstructor to be re-used if possible
        self.phase_reconstructor = value

    @pyqtSlot(object)
    def handle_fluor_reconstructor_update(self, value):
        # Saves phase reconstructor to be re-used if possible
        self.fluor_reconstructor = value

    @pyqtSlot(dict)
    def handle_meta_update(self, meta):
        with open(self.last_calib_meta_file, 'r') as file:
            current_json = json.load(file)

        for key, value in current_json['Microscope Parameters'].items():
            if key in meta:
                current_json['Microscope Parameters'][key] = meta[key]
            else:
                current_json['Microscope Parameters'][key] = None

        with open(self.last_calib_meta_file, 'w') as file:
            json.dump(current_json, file, indent=1)

    @pyqtSlot(str)
    def handle_calib_file_update(self, value):
        self.last_calib_meta_file = value

    @pyqtSlot(str)
    def handle_plot_sequence_update(self, value):
        current_idx = len(self.intensity_monitor)
        self.plot_sequence = (value, current_idx)

    @pyqtSlot(tuple)
    def handle_sat_slider_move(self, value):
        self.ui.le_sat_min.setText(str(np.round(value[0], 3)))
        self.ui.le_sat_max.setText(str(np.round(value[1], 3)))

    @pyqtSlot(tuple)
    def handle_val_slider_move(self, value):
        self.ui.le_val_min.setText(str(np.round(value[0], 3)))
        self.ui.le_val_max.setText(str(np.round(value[1], 3)))

    @pyqtSlot(str)
    def handle_reconstruction_store_update(self, value):

        self.reconstruction_data_path = value

    @pyqtSlot(tuple)
    def handle_reconstruction_dim_update(self, value):
        p, t, c = value
        layer_name = self.worker.manager.config.data_save_name

        if p == 0 and t == 0 and c == 0:
            self.reconstruction_data = WaveorderReader(self.reconstruction_data_path, 'zarr')
            self.viewer.add_image(self.reconstruction_data.get_zarr(p), name=layer_name + f'_Pos_{p:03d}')

            # self.viewer.dims.set_axis_label(0, 'P')
            self.viewer.dims.set_axis_label(0, 'T')
            self.viewer.dims.set_axis_label(1, 'C')
            self.viewer.dims.set_axis_label(2, 'Z')

        name = layer_name + f'_Pos_{p:03d}'
        if name not in self.viewer.layers:
            self.reconstruction_data = WaveorderReader(self.reconstruction_data_path, 'zarr')
            self.viewer.add_image(self.reconstruction_data.get_zarr(p), name=name)

        if not self.pause_updates:
            self.viewer.dims.set_current_step(0, t)
            self.viewer.dims.set_current_step(1, c)

        self.last_p = p

    @pyqtSlot(bool)
    def browse_dir_path(self):
        result = self._open_file_dialog(self.current_dir_path, 'dir')
        self.directory = result
        self.current_dir_path = result
        self.ui.le_directory.setText(result)
        self.ui.le_save_dir.setText(result)
        self.save_directory = result

    @pyqtSlot(bool)
    def browse_save_path(self):
        result = self._open_file_dialog(self.current_save_path, 'dir')
        self.save_directory = result
        self.current_save_path = result
        self.ui.le_save_dir.setText(result)

    @pyqtSlot(bool)
    def browse_data_dir(self):
        path = self._open_file_dialog(self.data_dir, 'dir')
        self.data_dir = path
        self.ui.le_data_dir.setText(self.data_dir)

        # reader = WaveorderReader(self.data_dir)
        # if reader.get_num_positions() > 1:
        #     self.ui.slider_positions.setDisabled(False)
        #     self._promote_slider_offline(self.ui.slider_positions, range_=(0, reader.get_num_positions()))
        # else:
        #     self.ui.slider_positions.setRange(0, 0)
        #     self.ui.slider_positions.setDisabled(True)
        #
        # if reader.frames > 1:
        #     self.ui.slider_timepoints.setDisabled(False)
        #     self._promote_slider_offline(self.ui.slider_timepoints, range_=(0, reader.frames))
        # else:
        #     self.ui.slider_timepoints.setRange(0, 0)
        #     self.ui.slider_timepoints.setDisabled(True)


    @pyqtSlot(bool)
    def browse_calib_meta(self):
        path = self._open_file_dialog(self.calib_path, 'file')
        self.calib_path = path
        self.ui.le_calibration_metadata.setText(self.calib_path)

    @pyqtSlot()
    def enter_dir_path(self):
        path = self.ui.le_directory.text()
        if os.path.exists(path):
            self.directory = path
            self.save_directory = path
            self.ui.le_save_dir.setText(path)
        else:
            self.ui.le_directory.setText('Path Does Not Exist')

    @pyqtSlot()
    def enter_swing(self):
        self.swing = float(self.ui.le_swing.text())

    @pyqtSlot()
    def enter_wavelength(self):
        self.wavelength = int(self.ui.le_wavelength.text())

    @pyqtSlot()
    def enter_calib_scheme(self):
        index = self.ui.cb_calib_scheme.currentIndex()
        if index == 0:
            self.calib_scheme = '4-State'
        else:
            self.calib_scheme = '5-State'

    @pyqtSlot()
    def enter_calib_mode(self):
        index = self.ui.cb_calib_mode.currentIndex()
        if index == 0:
            self.calib_mode = 'retardance'
            self.ui.label_lca.hide()
            self.ui.label_lcb.hide()
            self.ui.cb_lca.hide()
            self.ui.cb_lcb.hide()
        else:
            self.calib_mode = 'voltage'
            self.ui.cb_lca.clear()
            self.ui.cb_lcb.clear()
            self.ui.cb_lca.show()
            self.ui.cb_lcb.show()
            self.ui.label_lca.show()
            self.ui.label_lcb.show()

            cfg = self.mmc.getConfigData('Channel', 'State0')

            memory = set()
            for i in range(cfg.size()):
                prop = cfg.getSetting(i)
                if 'TS_DAC' in prop.getDeviceLabel():
                    dac = prop.getDeviceLabel()[-2:]
                    if dac not in memory:
                        self.ui.cb_lca.addItem('DAC'+dac)
                        self.ui.cb_lcb.addItem('DAC'+dac)
                        memory.add(dac)
                    else:
                        continue

    @pyqtSlot()
    def enter_dac_lca(self):
        dac = self.ui.cb_lca.currentText()
        self.lca_dac = dac

    @pyqtSlot()
    def enter_dac_lcb(self):
        dac = self.ui.cb_lcb.currentText()
        self.lcb_dac = dac

    @pyqtSlot()
    def enter_config_group(self):
        self.config_group = self.ui.cb_config_group.currentText()
        config = self.mmc.getAvailableConfigs(self.config_group)

        channels = []
        for i in range(config.size()):
            channels.append(config.get(i))

        states = ['State0', 'State1', 'State2', 'State3', 'State4']
        missing = []
        for state in states:
            if state not in channels:
                missing.append(state)

        if len(missing) != 0:
            msg = f'The chosen config group ({self.config_group}) is missing states: {missing}. '\
                   'Please refer to the recOrder wiki on how to set up the config properly.'

            self.ui.cb_config_group.setStyleSheet("border: 1px solid rgb(200,0,0);")
            raise KeyError(msg)
        else:
            self.ui.cb_config_group.setStyleSheet("")

    @pyqtSlot()
    def enter_use_cropped_roi(self):
        state = self.ui.chb_use_roi.checkState()
        if state == 2:
            self.use_cropped_roi = True
        elif state == 0:
            self.use_cropped_roi = False

    @pyqtSlot()
    def enter_bg_folder_name(self):
        self.bg_folder_name = self.ui.le_bg_folder.text()

    @pyqtSlot()
    def enter_n_avg(self):
        self.n_avg = int(self.ui.le_n_avg.text())

    @pyqtSlot()
    def enter_log_level(self):
        index = self.ui.cb_loglevel.currentIndex()
        if index == 0:
            logging.getLogger().setLevel(logging.INFO)
        else:
            logging.getLogger().setLevel(logging.DEBUG)

    @pyqtSlot()
    def enter_save_path(self):
        path = self.ui.le_save_dir.text()
        if os.path.exists(path):
            self.save_directory = path
            self.current_save_path = path
        else:
            self.ui.le_save_dir.setText('Path Does Not Exist')

    @pyqtSlot()
    def enter_save_name(self):
        name = self.ui.le_data_save_name.text()
        self.save_name = name

    @pyqtSlot()
    def enter_zstart(self):
        self.z_start = float(self.ui.le_zstart.text())

    @pyqtSlot()
    def enter_zend(self):
        self.z_end = float(self.ui.le_zend.text())

    @pyqtSlot()
    def enter_zstep(self):
        self.z_step = float(self.ui.le_zstep.text())

    @pyqtSlot()
    def enter_birefringence_dim(self):
        state = self.ui.cb_birefringence.currentIndex()
        if state == 0:
            self.birefringence_dim = '2D'
        elif state == 1:
            self.birefringence_dim = '3D'

    @pyqtSlot()
    def enter_phase_dim(self):
        state = self.ui.cb_phase.currentIndex()
        if state == 0:
            self.phase_dim = '2D'
        elif state == 1:
            self.phase_dim = '3D'

    @pyqtSlot()
    def enter_phase_denoiser(self):
        state = self.ui.cb_phase_denoiser.currentIndex()
        if state == 0:
            self.ui.label_itr.setHidden(True)
            self.ui.label_phase_rho.setHidden(True)
            self.ui.le_rho.setHidden(True)
            self.ui.le_itr.setHidden(True)

        elif state == 1:
            self.ui.label_itr.setHidden(False)
            self.ui.label_phase_rho.setHidden(False)
            self.ui.le_rho.setHidden(False)
            self.ui.le_itr.setHidden(False)

    @pyqtSlot()
    def enter_acq_bg_path(self):
        path = self.ui.le_bg_path.text()
        if os.path.exists(path):
            self.acq_bg_directory = path
            self.current_bg_path = path
        else:
            self.ui.le_bg_path.setText('Path Does Not Exist')

    @pyqtSlot(bool)
    def browse_acq_bg_path(self):
        result = self._open_file_dialog(self.current_bg_path, 'dir')
        self.acq_bg_directory = result
        self.current_bg_path = result
        self.ui.le_bg_path.setText(result)

    @pyqtSlot()
    def enter_bg_correction(self):
        state = self.ui.cb_bg_method.currentIndex()
        if state == 0:
            self.ui.label_bg_path.setHidden(True)
            self.ui.le_bg_path.setHidden(True)
            self.ui.qbutton_browse_bg_path.setHidden(True)
            self.bg_option = 'None'
        elif state == 1:
            self.ui.label_bg_path.setHidden(False)
            self.ui.le_bg_path.setHidden(False)
            self.ui.qbutton_browse_bg_path.setHidden(False)
            self.bg_option = 'Global'
        elif state == 2:
            self.ui.label_bg_path.setHidden(False)
            self.ui.le_bg_path.setHidden(False)
            self.ui.qbutton_browse_bg_path.setHidden(False)
            self.bg_option = 'local_fit'

    @pyqtSlot()
    def enter_gpu_id(self):
        self.gpu_id = int(self.ui.le_gpu_id.text())

    @pyqtSlot()
    def enter_use_gpu(self):
        state = self.ui.chb_use_gpu.checkState()
        if state == 2:
            self.use_gpu = True
        elif state == 0:
            self.use_gpu = False

    @pyqtSlot()
    def enter_obj_na(self):
        self.obj_na = float(self.ui.le_obj_na.text())

    @pyqtSlot()
    def enter_cond_na(self):
        self.cond_na = float(self.ui.le_cond_na.text())

    @pyqtSlot()
    def enter_mag(self):
        self.mag = float(self.ui.le_mag.text())

    @pyqtSlot()
    def enter_ps(self):
        self.ps = float(self.ui.le_ps.text())

    @pyqtSlot()
    def enter_n_media(self):
        self.n_media = float(self.ui.le_n_media.text())

    @pyqtSlot()
    def enter_pad_z(self):
        self.pad_z = int(self.ui.le_pad_z.text())

    @pyqtSlot()
    def enter_pause_updates(self):
        state = self.ui.chb_pause_updates.checkState()
        if state == 2:
            self.pause_updates = True
        elif state == 0:
            self.pause_updates = False

    @pyqtSlot(int)
    def enter_method(self):
        idx = self.ui.cb_method.currentIndex()


        if idx == 0:
            self.method = 'QLIPP'
            self.ui.le_cond_na.show()
            self.ui.label_cond_na.show()
            self.ui.cb_bg_method.show()
            self.ui.le_bg_path.hide() if self.bg_option == 'None' else self.ui.le_bg_path.show()
            self.ui.qbutton_browse_bg_path.hide() if self.bg_option == 'None' else self.ui.qbutton_browse_bg_path.show()
            self.ui.label_bg_path.hide() if self.bg_option == 'None' else self.ui.label_bg_path.show()
            self.ui.label_bg_method.hide() if self.bg_option == 'None' else self.ui.label_bg_method.show()
            self.ui.phase.show()
            self.ui.fluor.show()
            self.ui.label_chan_desc.setText('Retardance, Orientation, BF, Phase3D, Phase2D, Fluor1, '
                                            'Fluor2 (ex. DAPI, GFP), S0, S1, S2, S3')

        elif idx == 1:
            self.method = 'PhaseFromBF'
            self.ui.le_cond_na.show()
            self.ui.label_cond_na.show()
            self.ui.cb_bg_method.show()
            self.ui.le_bg_path.hide() if self.bg_option == 'None' else self.ui.le_bg_path.show()
            self.ui.qbutton_browse_bg_path.hide() if self.bg_option == 'None' else self.ui.qbutton_browse_bg_path.show()
            self.ui.label_bg_path.hide() if self.bg_option == 'None' else self.ui.label_bg_path.show()
            self.ui.label_bg_method.hide() if self.bg_option == 'None' else self.ui.label_bg_method.show()
            self.ui.phase.show()
            self.ui.fluor.show()
            self.ui.label_fluor_chan.setText('Brightfield Channel Index')
            self.ui.le_fluor_chan.setPlaceholderText('int')
            self.ui.label_chan_desc.setText('Phase3D, Phase2D, Fluor1, Fluor2 (ex. DAPI, GFP)')

        else:
            self.method = 'FluorDeconv'
            self.ui.le_cond_na.hide()
            self.ui.label_cond_na.hide()
            self.ui.cb_bg_method.hide()
            self.ui.label_bg_path.hide()
            self.ui.label_bg_method.hide()
            self.ui.le_bg_path.hide()
            self.ui.qbutton_browse_bg_path.hide()
            self.ui.phase.hide()
            self.ui.fluor.hide()
            self.ui.label_fluor_chan.setText('Fluor Channel Index')
            self.ui.le_fluor_chan.setPlaceholderText('list of integers or int')
            self.ui.label_chan_desc.setText('Fluor1, Fluor2 (ex. DAPI, GFP)')

    @pyqtSlot(int)
    def enter_mode(self):
        idx = self.ui.cb_mode.currentIndex()

        if idx == 0:
            self.mode = '3D'
        else:
            self.mode = '2D'

    @pyqtSlot()
    def enter_data_dir(self):
        entry = self.ui.le_data_dir.text()
        if not os.path.exists(entry):
            self.ui.le_data_dir.setStyleSheet("border: 1px solid rgb(200,0,0);")
            self.ui.le_data_dir.setText('Path Does Not Exist')
        else:
            self.ui.le_data_dir.setStyleSheet("")
            self.data_dir = entry

        # reader = WaveorderReader(self.data_dir)
        # if reader.get_num_positions() > 1:
        #     self.ui.slider_positions.setDisabled(False)
        #     self.ui.chb_positions.setDisabled(False)
        #     self._promote_slider_offline(self.ui.slider_positions, range_=(0, reader.get_num_positions()))
        # else:
        #     self.ui.slider_positions.setRange(0, 0)
        #     self.ui.slider_positions.setDisabled(True)
        #     self.ui.chb_positions.setDisabled(True)
        #
        # if reader.frames > 1:
        #     self.ui.slider_timepoints.setDisabled(False)
        #     self.ui.chb_timepoints.setDisabled(False)
        #     self._promote_slider_offline(self.ui.slider_timepoints, range_=(0, reader.frames))
        # else:
        #     self.ui.slider_timepoints.setRange(0, 0)
        #     self.ui.slider_timepoints.setDisabled(True)
        #     self.ui.chb_timepoints.setDisabled(True)

    @pyqtSlot()
    def enter_calib_meta(self):
        entry = self.ui.le_calibration_metadata.text()
        if not os.path.exists(entry):
            self.ui.le_calibration_metadata.setStyleSheet("border: 1px solid rgb(200,0,0);")
            self.ui.le_calibration_metadata.setText('Path Does Not Exist')
        else:
            self.ui.le_calibration_metadata.setStyleSheet("")
            self.calib_path = entry

    @pyqtSlot()
    def enter_single_position(self):

        state = self.ui.chb_positions.checkState()
        current_slider_range = (self.ui.slider_positions.minimum(), self.ui.slider_positions.maximum())

        if state == 2:
            self._demote_slider_offline(self.ui.slider_positions, range_=current_slider_range)
        else:
            self._promote_slider_offline(self.ui.slider_positions, range_=current_slider_range)

    @pyqtSlot()
    def enter_single_timepoint(self):

        state = self.ui.chb_timepoints.checkState()
        current_slider_range = (self.ui.slider_timepoints.minimum(), self.ui.slider_timepoints.maximum())

        if state == 2:
            self._demote_slider_offline(self.ui.slider_timepoints, range_=current_slider_range)
        else:
            self._promote_slider_offline(self.ui.slider_timepoints, range_=current_slider_range)

    @pyqtSlot()
    def enter_colormap(self):
        prev_cmap = self.colormap
        state = self.ui.cb_colormap.currentIndex()
        if state == 0:
            self.ui.label_orientation_image.setPixmap(self.jch_pixmap)
            self.colormap = 'JCh'
        else:
            self.ui.label_orientation_image.setPixmap(self.hsv_pixmap)
            self.colormap = 'HSV'

        # Update the birefringence overlay to new colormap if the colormap has changed
        if prev_cmap != self.colormap:
            #TODO: Handle case where there are multiple snaps
            if 'BirefringenceOverlay2D' in self.viewer.layers:
                if 'Retardance2D' in self.viewer.layers and 'Orientation2D' in self.viewer.layers:

                    overlay = ret_ori_overlay(retardance=self.viewer.layers['Retardance2D'].data,
                                              orientation=self.viewer.layers['Orientation2D'].data,
                                              scale=(0, np.percentile(self.viewer.layers['Retardance2D'].data, 99.99)),
                                              cmap=self.colormap)

                    self.viewer.layers['BirefringenceOverlay2D'].data = overlay

    @pyqtSlot(int)
    def enter_use_full_volume(self):
        state = self.ui.chb_display_volume.checkState()

        if state == 2:
            self.ui.le_overlay_slice.clear()
            self.ui.le_overlay_slice.setEnabled(False)
            self.use_full_volume = False
        else:
            self.ui.le_overlay_slice.setEnabled(True)
            self.use_full_volume = True

    @pyqtSlot()
    def enter_display_slice(self):
        slice = int(self.ui.le_overlay_slice.text())
        self.display_slice = slice

    @pyqtSlot()
    def enter_sat_min(self):
        val = float(self.ui.le_sat_min.text())
        slider_val = self.ui.slider_saturation.value()
        self.ui.slider_saturation.setValue((val, slider_val[1]))

    @pyqtSlot()
    def enter_sat_max(self):
        val = float(self.ui.le_sat_max.text())
        slider_val = self.ui.slider_saturation.value()
        self.ui.slider_saturation.setValue((slider_val[0], val))

    @pyqtSlot()
    def enter_val_min(self):
        val = float(self.ui.le_val_min.text())
        slider_val = self.ui.slider_value.value()
        self.ui.slider_value.setValue((val, slider_val[1]))

    @pyqtSlot()
    def enter_val_max(self):
        val = float(self.ui.le_val_max.text())
        slider_val = self.ui.slider_value.value()
        self.ui.slider_value.setValue((slider_val[0], val))

    @pyqtSlot(bool)
    def push_note(self):

        if not self.last_calib_meta_file:
            raise ValueError('No calibration has been performed yet so there is no previous metadata file')
        else:
            note = self.ui.le_notes_field.text()

            with open(self.last_calib_meta_file, 'r') as file:
                current_json = json.load(file)

            old_note = current_json['Notes']
            if old_note is None or old_note == '' or old_note == note:
                current_json['Notes'] = note
            else:
                current_json['Notes'] = old_note + ', ' + note

            with open(self.last_calib_meta_file, 'w') as file:
                json.dump(current_json, file, indent=1)

    @pyqtSlot(bool)
    def calc_extinction(self):

        # Snap images from the extinction state and first elliptical state
        set_lc_state(self.mmc, self.config_group, 'State0')
        extinction = snap_and_average(self.calib.snap_manager)
        set_lc_state(self.mmc, self.config_group, 'State1')
        state1 = snap_and_average(self.calib.snap_manager)

        # Calculate extinction based off captured intensities
        extinction = self.calib.calculate_extinction(self.swing, self.calib.I_Black, extinction, state1)
        self.ui.le_extinction.setText(str(extinction))

    @pyqtSlot(bool)
    def load_calibration(self):
        """
        Uses previous JSON calibration metadata to load previous calibration
        """
        result = self._open_file_dialog(self.current_dir_path, 'file')
        with open(result, 'r') as file:
            meta = json.load(file)

        # Update Properties
        self.wavelength = meta['Summary']['Wavelength (nm)']
        self.swing = meta['Summary']['Swing (fraction)']

        # Initialize calibration class
        self.calib = QLIPP_Calibration(self.mmc, self.mm, group=self.config_group)
        self.calib.swing = self.swing
        self.ui.le_swing.setText(str(self.swing))
        self.calib.wavelength = self.wavelength
        self.ui.le_wavelength.setText(str(self.wavelength))

        # Update Calibration Scheme Combo Box
        if meta['Summary']['Acquired Using'] == '4-State':
            self.ui.cb_calib_scheme.setCurrentIndex(0)
        else:
            self.ui.cb_calib_scheme.setCurrentIndex(1)

        self.last_calib_meta_file = result

        params = meta['Microscope Parameters']
        if params is not None:
            self.ui.le_pad_z.setText(str(params['pad_z']) if params['pad_z'] is not None else '')
            self.ui.le_n_media.setText(str(params['n_objective_media']) if params['n_objective_media'] is not None else '')
            self.ui.le_obj_na.setText(str(params['objective_NA']) if params['objective_NA'] is not None else '')
            self.ui.le_cond_na.setText(str(params['condenser_NA']) if params['condenser_NA'] is not None else '')
            self.ui.le_mag.setText(str(params['magnification']) if params['magnification'] is not None else '')
            self.ui.le_ps.setText(str(params['pixel_size']) if params['pixel_size'] is not None else '')

        # Move the load calibration function to a separate thread
        self.worker = load_calibration(self.calib, meta)

        def update_extinction(extinction):
            self.calib.extinction_ratio = float(extinction)

        # initialize worker properties
        self.ui.qbutton_stop_calib.clicked.connect(self.worker.quit)
        self.worker.yielded.connect(self.ui.le_extinction.setText)
        self.worker.yielded.connect(update_extinction)
        self.worker.returned.connect(self._update_calib)
        self.worker.errored.connect(self._handle_error)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.finished.connect(self._handle_load_finished)
        self.worker.start()

    @pyqtSlot(bool)
    def run_calibration(self):
        """
        Wrapper function to create calibration worker and move that worker to a thread.
        Calibration is then executed by the calibration worker
        """

        self.calib = QLIPP_Calibration(self.mmc, self.mm, mode=self.calib_mode)

        if self.calib_mode == 'voltage':
            self.calib.set_dacs(self.lca_dac, self.lcb_dac)

        # Reset Styling
        self.ui.tb_calib_assessment.setText('')
        self.ui.tb_calib_assessment.setStyleSheet("")

        # Save initial autoshutter state for when we set it back later
        self.auto_shutter = self.mmc.getAutoShutter()

        logging.info('Starting Calibration')

        # Initialize displays + parameters for calibration
        self.ui.progress_bar.setValue(0)
        self.plot_item.clear()
        self.intensity_monitor = []
        self.calib.swing = self.swing
        self.calib.wavelength = self.wavelength
        self.calib.meta_file = os.path.join(self.directory, 'calibration_metadata.txt')

        # Make sure Live Mode is off
        if self.calib.snap_manager.getIsLiveModeOn():
            self.calib.snap_manager.setLiveModeOn(False)

        # Init Worker and Thread
        self.worker = CalibrationWorker(self, self.calib)

        # Connect Handlers
        self.worker.progress_update.connect(self.handle_progress_update)
        self.worker.extinction_update.connect(self.handle_extinction_update)
        self.worker.intensity_update.connect(self.handle_plot_update)
        self.worker.calib_assessment.connect(self.handle_calibration_assessment_update)
        self.worker.calib_assessment_msg.connect(self.handle_calibration_assessment_msg_update)
        self.worker.calib_file_emit.connect(self.handle_calib_file_update)
        self.worker.plot_sequence_emit.connect(self.handle_plot_sequence_update)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.errored.connect(self._handle_error)
        self.ui.qbutton_stop_calib.clicked.connect(self.worker.quit)

        self.worker.start()

    @pyqtSlot(bool)
    def capture_bg(self):
        """
        Wrapper function to capture a set of background images.  Will snap images and display reconstructed
        birefringence.  Check connected handlers for napari display.

        Returns
        -------

        """

        # Init worker and thread
        self.worker = BackgroundCaptureWorker(self, self.calib)

        # Connect Handlers
        self.worker.bg_image_emitter.connect(self.handle_bg_image_update)
        self.worker.bire_image_emitter.connect(self.handle_bg_bire_image_update)

        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.errored.connect(self._handle_error)
        self.ui.qbutton_stop_calib.clicked.connect(self.worker.quit)
        self.worker.aborted.connect(self._handle_calib_abort)

        # Start Capture Background Thread
        self.worker.start()

    @pyqtSlot(bool)
    def acq_birefringence(self):
        """
        Wrapper function to acquire birefringence stack/image and plot in napari
        Returns
        -------

        """

        self._check_requirements_for_acq('birefringence')

        # Init Worker and thread
        self.worker = PolarizationAcquisitionWorker(self, self.calib, 'birefringence')

        # Connect Handler
        self.worker.bire_image_emitter.connect(self.handle_bire_image_update)
        self.worker.meta_emitter.connect(self.handle_meta_update)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.errored.connect(self._handle_acq_error)

        # Start Thread
        self.worker.start()

    @pyqtSlot(bool)
    def acq_phase(self):
        """
        Wrapper function to acquire phase stack and plot in napari
        """

        self._check_requirements_for_acq('phase')

        # Init worker and thread
        self.worker = PolarizationAcquisitionWorker(self, self.calib, 'phase')

        # Connect Handlers
        self.worker.phase_image_emitter.connect(self.handle_phase_image_update)
        self.worker.phase_reconstructor_emitter.connect(self.handle_qlipp_reconstructor_update)
        self.worker.meta_emitter.connect(self.handle_meta_update)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.errored.connect(self._handle_acq_error)

        # Start thread
        self.worker.start()

    @pyqtSlot(bool)
    def acq_birefringence_phase(self):
        """
        Wrapper function to acquire both birefringence and phase stack and plot in napari
        """

        self._check_requirements_for_acq('phase')

        # Init worker
        self.worker = PolarizationAcquisitionWorker(self, self.calib, 'all')

        # connect handlers
        self.worker.phase_image_emitter.connect(self.handle_phase_image_update)
        self.worker.bire_image_emitter.connect(self.handle_bire_image_update)
        self.worker.phase_reconstructor_emitter.connect(self.handle_qlipp_reconstructor_update)
        self.worker.meta_emitter.connect(self.handle_meta_update)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.errored.connect(self._handle_acq_error)
        self.ui.qbutton_stop_acq.clicked.connect(self.worker.quit)

        # Start Thread
        self.worker.start()

    @pyqtSlot(bool)
    def acquire_fluor_deconvolved(self):

        self._check_requirements_for_acq('fluor')

        # Init worker
        self.worker = FluorescenceAcquisitionWorker(self)

        # connect handlers
        self.worker.fluor_image_emitter.connect(self.handle_fluor_image_update)
        self.worker.meta_emitter.connect(self.handle_meta_update)
        self.worker.fluor_reconstructor_emitter.connect(self.handle_fluor_reconstructor_update)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.errored.connect(self._handle_acq_error)
        self.ui.qbutton_stop_acq.clicked.connect(self.worker.quit)

        # Start Thread
        self.worker.start()


    @pyqtSlot(bool)
    def listen_and_reconstruct(self):

        # Init reconstructor
        if self.bg_option != 'None':
            with open(os.path.join(self.current_bg_path, 'calibration_metadata.txt')) as file:
                js = json.load(file)
                roi = js['Summary']['ROI Used (x, y, width, height)']
                height, width = roi[2], roi[3]
            bg_data = load_bg(self.current_bg_path, height, width, roi)
        else:
            bg_data = None

        # Init worker
        self.worker = ListeningWorker(self, bg_data)

        # connect handlers
        self.worker.store_emitter.connect(self.add_listener_data)
        self.worker.dim_emitter.connect(self.update_dims)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.finished.connect(self._reset_listening)
        self.worker.errored.connect(self._handle_acq_error)
        self.ui.qbutton_stop_acq.clicked.connect(self.worker.quit)

        # Start Thread
        self.worker.start()

    @pyqtSlot(bool)
    def reconstruct(self):

        success = self._check_requirements_for_reconstruction()
        if not success:
            raise ValueError('Please make sure all necessary parameters are set before reconstruction')

        self._populate_config_from_app()
        self.config_reader.data_type = 'ometiff' #TODO: Get rid of this for new waveorder
        self.worker = ReconstructionWorker(self, self.config_reader)

        # connect handlers
        self.worker.dimension_emitter.connect(self.handle_reconstruction_dim_update)
        self.worker.store_emitter.connect(self.handle_reconstruction_store_update)
        self.worker.started.connect(self._disable_buttons)
        self.worker.finished.connect(self._enable_buttons)
        self.worker.errored.connect(self._handle_acq_error)
        self.ui.qbutton_stop_acq.clicked.connect(self.worker.quit)

        self.worker.start()

    @pyqtSlot(bool)
    def save_config(self):
        path = self._open_file_dialog(self.save_config_path, 'save')
        self.save_config_path = path
        name = PurePath(self.save_config_path).name
        dir_ = self.save_config_path.strip(name)
        self._populate_config_from_app()
        self.config_reader.save_yaml(dir_=dir_, name=name)

    @pyqtSlot(bool)
    def load_config(self):
        path = self._open_file_dialog(self.save_config_path, 'file')
        if path == '':
            pass
        else:
            self.config_path = path
            self.config_reader = ConfigReader(self.config_path)
            self._populate_from_config()

    @pyqtSlot(bool)
    def load_default_config(self):
        self.config_reader = ConfigReader(mode='3D', method='QLIPP')
        self._populate_from_config()

    @pyqtSlot(int)
    def update_sat_scale(self):
        idx = self.ui.cb_saturation.currentIndex()
        layer = self.ui.cb_saturation.itemText(idx)
        data = self.viewer.layers[layer].data
        min_, max_ = np.min(data), np.max(data)
        self.ui.slider_saturation.setMinimum(min_)
        self.ui.slider_saturation.setMaximum(max_)
        # self.ui.slider_value.setRange(min_, max_)
        self.ui.slider_saturation.setSingleStep((max_ - min_)/250)
        self.ui.slider_saturation.setValue((min_, max_))
        self.ui.le_sat_max.setText(str(np.round(max_, 3)))
        self.ui.le_sat_min.setText(str(np.round(min_, 3)))

    @pyqtSlot(int)
    def update_value_scale(self):
        idx = self.ui.cb_value.currentIndex()
        layer = self.ui.cb_value.itemText(idx)
        data = self.viewer.layers[layer].data
        min_, max_ = np.min(data), np.max(data)
        self.ui.slider_value.setMinimum(min_)
        self.ui.slider_value.setMaximum(max_)
        # self.ui.slider_value.setRange(min_, max_)
        self.ui.slider_value.setSingleStep((max_ - min_)/250)
        self.ui.slider_value.setValue((min_, max_))
        self.ui.le_val_max.setText(str(np.round(max_, 3)))
        self.ui.le_val_min.setText(str(np.round(min_, 3)))

    @pyqtSlot(bool)
    def create_overlay(self):

        if self.ui.cb_hue.count() == 0 or self.ui.cb_saturation.count() == 0 or self.ui.cb_value == 0:
            raise ValueError('Cannot create overlay until all 3 combo boxes are populated')

        H = self.viewer.layers[self.ui.cb_hue.itemText(self.ui.cb_hue.currentIndex())].data
        S = self.viewer.layers[self.ui.cb_saturation.itemText(self.ui.cb_saturation.currentIndex())].data
        V = self.viewer.layers[self.ui.cb_value.itemText(self.ui.cb_value.currentIndex())].data


        #TODO: this is a temp fix which handles on data with n-dimensions of 4, 3, or 2 which automatically
        # chooses the first timepoint
        if H.ndim > 2 or S.ndim > 2 or V.ndim > 2:
            if H.ndim == 4:
                # assumes this is a (T, Z, Y, X) array read from napari-ome-zarr
                H = H[0, self.display_slice] if not self.use_full_volume else H[0]
            if S.ndim == 4:
                S = S[0, self.display_slice] if not self.use_full_volume else S[0]
            if V.ndim == 4:
                V = V[0, self.display_slice] if not self.use_full_volume else V[0]

            if H.ndim == 3:
                # assumes this is a (Z, Y, X) array collected from acquisition module
                H = H[self.display_slice] if not self.use_full_volume else H

            if S.ndim == 3:
                S = S[self.display_slice] if not self.use_full_volume else S

            if S.ndim == 3:
                S = S[self.display_slice] if not self.use_full_volume else S

        mode = '2D' if not self.use_full_volume else '3D'

        H_name = self.ui.cb_hue.itemText(self.ui.cb_hue.currentIndex())
        H_scale = (np.min(H), np.max(H)) if 'Orientation' not in H_name else (0, np.pi)
        S_scale = self.ui.slider_saturation.value()
        V_scale = self.ui.slider_value.value()

        hsv_image = generic_hsv_overlay(H, S, V, H_scale, S_scale, V_scale, mode=mode)

        idx = 0
        while f'HSV_Overlay_{idx}' in self.viewer.layers:
            idx += 1

        self.viewer.add_image(hsv_image, name=f'HSV_Overlay_{idx}', rgb=True)

    @pyqtSlot(object)
    def add_listener_data(self, store):

        self.viewer.add_image(store['Birefringence'], name=self.worker.prefix)
        self.viewer.dims.set_axis_label(0, 'P')
        self.viewer.dims.set_axis_label(1, 'T')
        self.viewer.dims.set_axis_label(2, 'C')
        self.viewer.dims.set_axis_label(3, 'Z')

    @pyqtSlot(tuple)
    def update_dims(self, dims):

        if not self.pause_updates:
            self.viewer.dims.set_current_step(0, dims[0])
            self.viewer.dims.set_current_step(1, dims[1])
            self.viewer.dims.set_current_step(3, dims[2])
        else:
            pass

    def _reset_listening(self):
        self.listening_reconstructor = None
        self.listening_store = None

    # def _open_browse_dialog(self, default_path, file=False):
    #
    #     if not file:
    #         return self._open_dir_dialog("select a directory",
    #                                      default_path)
    #     else:
    #         return self._open_file_dialog('Please select a file',
    #                                       default_path)
    #
    # def _open_dir_dialog(self, title, ref):
    #     options = QFileDialog.Options()
    #
    #     options |= QFileDialog.DontUseNativeDialog
    #     path = QFileDialog.getExistingDirectory(None,
    #                                             title,
    #                                             ref,
    #                                             options=options)
    #     return path
    #
    # def _open_file_dialog(self, title, ref):
    #     options = QFileDialog.Options()
    #
    #     options |= QFileDialog.DontUseNativeDialog
    #     path = QFileDialog.getOpenFileName(None,
    #                                        title,
    #                                        ref,
    #                                        options=options)[0]
    #     return path

    def _open_file_dialog(self, default_path, type):

        return self._open_dialog("select a directory",
                                 str(default_path),
                                 type)

    def _open_dialog(self, title, ref, type):
        options = QFileDialog.Options()

        options |= QFileDialog.DontUseNativeDialog
        if type == 'dir':
            path = QFileDialog.getExistingDirectory(None,
                                                    title,
                                                    ref,
                                                    options=options)
        elif type == 'file':
            path = QFileDialog.getOpenFileName(None,
                                               title,
                                               ref,
                                               options=options)[0]
        elif type == 'save':
            path = QFileDialog.getSaveFileName(None,
                                               'Choose a save name',
                                               ref,
                                               options=options)[0]
        else:
            raise ValueError('Did not understand file dialogue type')

        return path


class QtLogger(logging.Handler):
    """
    Class to changing logging handler to the napari log output display
    """

    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    # emit function necessary to be considered a logging handler
    def emit(self, record):
        msg = self.format(record)
        self.widget.appendPlainText(msg)
