from recOrder.io.config_reader import ConfigReader
from waveorder.io.reader import WaveorderReader
from waveorder.io.writer import WaveorderWriter
import time
import os
import shutil
from recOrder.pipelines.qlipp_pipeline import QLIPP
from recOrder.pipelines.phase_from_bf_pipeline import PhaseFromBF
from recOrder.pipelines.fluor_deconv import FluorescenceDeconvolution
from recOrder.postproc.post_processing import *
from recOrder.preproc.pre_processing import *


class PipelineManager:
    """
    This will pull the necessary pipeline based off the config default.
    """

    def __init__(self, config: ConfigReader, overwrite: bool = False):

        start = time.time()
        print('Reading Data...')
        data = WaveorderReader(config.data_dir, config.data_type, extract_data=True)
        end = time.time()
        print(f'Finished Reading Data ({(end - start) / 60 :0.1f} min)')

        self.config = config
        self.data = data
        self.use_hcs = True if self.config.data_type == 'zarr' else False

        self._gen_coord_set()

        # This section creates the HCS metadata that exists from the raw data zarr store and updates
        # if it you are only doing a subset of positions.  Allows for consistent format between
        # raw data and processed data
        if self.use_hcs:
            self.hcs_meta = self.data.reader.hcs_meta
            self.hcs_meta = self._update_hcs_meta_from_config(self.hcs_meta)
        else:
            self.hcs_meta = None

        # Delete previous data if overwrite is true, helpful for making quick config changes since the writer
        # doesn't by default allow you to overwrite data
        if overwrite:
            path = os.path.join(self.config.save_dir, self.config.data_save_name)
            path = path+'.zarr' if not path.endswith('.zarr') else path
            if os.path.exists(path):
                shutil.rmtree(path)

        # Writer Parameters
        self.writer = WaveorderWriter(self.config.save_dir, hcs=self.use_hcs, hcs_meta=self.hcs_meta, verbose=False)
        self.writer.create_zarr_root(self.config.data_save_name)

        # Pipeline Initiation
        if self.config.method == 'QLIPP':
            self.pipeline = QLIPP(self.config, self.data, self.writer, self.config.mode, self.num_t)

        elif self.config.method == 'PhaseFromBF':
            self.pipeline = PhaseFromBF(self.config, self.data, self.writer, self.num_t)

        elif self.config.method == 'FluorDeconv':
            self.pipeline = FluorescenceDeconvolution(self.config, self.data, self.writer, self.config.mode, self.num_t)

        elif self.config.method == 'UPTI':
            raise NotImplementedError

        elif self.config.method == 'IPS':
            raise NotImplementedError

    def _update_hcs_meta_from_config(self, hcs_meta):
        """
        If HCS metadata was found in raw data, make sure that it is consistent
        with the data being requested for reconstruction

        Parameters
        ----------
        hcs_meta:       (dict) HCS metadata as needed by the writer

        Returns
        -------
        new_meta:       (dict) HCS metadata corresponding to config parameters (or unchanged)

        """

        # make a list of empty wells the size of the # of positions
        wells_new = [None] * (max(self.p_indices) + 1)
        well_meta_new = [None] * (max(self.p_indices) + 1)

        meta_new = hcs_meta.copy()
        for idx, p in enumerate(self.p_indices):

            # for every position, grab it from the original HCS metadata and place it into new list
            well_meta = meta_new['well']
            well_meta_new[idx] = well_meta[p]

            wells = meta_new['plate']['wells']
            wells_new[idx] = wells[p]

        # Filter out any blank entries
        wells_new = list(filter(None, wells_new))
        well_meta_new = list(filter(None, well_meta_new))

        # based on new well metadata, grab the rows/columns names
        rows_new = []
        cols_new = []
        for well in wells_new:
            split = well['path'].split('/')
            if not {'name': split[0]} in rows_new:
                rows_new.append({'name': split[0]})

            if not {'name': split[1]} in cols_new:
                cols_new.append({'name': split[1]})

        # Group all of the metadata together in one dictionary
        meta_new['plate']['rows'] = rows_new
        meta_new['plate']['columns'] = cols_new
        meta_new['plate']['wells'] = wells_new
        meta_new['well'] = well_meta_new

        return meta_new

    def _get_preprocessing_params(self):
        """
        method to get pre-processing functions and parameters.
        Only supports denoising at the moment

        Returns
        -------
        denoise_params:     (list) [[channels, thresholds, levels]]

        """
        # CAN ADD OTHER PREPROC FUNCTIONS IN FUTURE
        denoise_params = []
        if self.config.preprocessing.denoise_use:
            for i in range(len(self.config.preprocessing.denoise_channels)):
                threshold = 0.1 if self.config.preprocessing.denoise_threshold is None \
                    else self.config.preprocessing.denoise_threshold[i]
                level = 1 if self.config.preprocessing.denoise_level is None \
                    else self.config.preprocessing.denoise_level[i]

                denoise_params.append([self.config.preprocessing.denoise_channels[i], threshold, level])

            return denoise_params

        else:
            return None

    def _get_postprocessing_params(self):
        """
        Method to gather parameters for post_processing functions.
        Currently only supports denoising, registration

        CAN ADD MORE IN FUTURE

        Returns
        -------
        denoise_params:         (list) [[channel, threshold, levels]]

        registration_params:    (list) [[channel, shift]]

        """

        denoise_params = []
        if self.config.postprocessing.denoise_use:
            for i in range(len(self.config.postprocessing.denoise_channels)):
                threshold = 0.1 if self.config.postprocessing.denoise_threshold is None \
                    else self.config.postprocessing.denoise_threshold[i]
                level = 1 if self.config.postprocessing.denoise_level is None \
                    else self.config.postprocessing.denoise_level[i]

                denoise_params.append([self.config.postprocessing.denoise_channels[i], threshold, level])

        else:
            denoise_params = None

        registration_params = []
        if self.config.postprocessing.registration_use:
            for i in range(len(self.config.postprocessing.registration_channel_idx)):
                registration_params.append([self.config.postprocessing.registration_channel_idx[i],
                                            self.config.postprocessing.registration_shift[i]])
        else:
            registration_params = None

        return denoise_params, registration_params

    def _gen_coord_set(self):
        """
        Function creates a set of all position, time values to loop through for reconstruction

        Returns
        -------

        """

        # keep track of the order in which the p_indices come in for use later in the writer
        self.indices_map = dict()
        self.pt_set = set()
        p_indices = set()
        t_indices = set()

        # run through the different possible config specifications (ranges, single entries, 'all', etc.)
        cnt = 0
        for p_entry in self.config.positions:
            if p_entry == 'all':
                for p in range(self.data.get_num_positions()):
                    p_indices.add(p)
                    self.indices_map[p] = cnt
                    cnt += 1
                break
            elif isinstance(p_entry, int):
                p_indices.add(p_entry)
                self.indices_map[p_entry] = cnt
                cnt += 1
            elif isinstance(p_entry, list):
                for p in p_entry:
                    p_indices.add(p)
                    self.indices_map[p_entry] = cnt
                    cnt += 1
            elif isinstance(p_entry, tuple):
                for p in range(p_entry[0], p_entry[1]):
                    p_indices.add(p)
                    self.indices_map[p] = cnt
                    cnt += 1
            else:
                raise ValueError(f'Did not understand entry {p_entry} in config specified positions')

        # run through the different possible config specifications (ranges, single entries, 'all', etc.)
        for t_entry in self.config.timepoints:
            if t_entry == 'all':
                for t in range(self.data.frames):
                    t_indices.add(t)
                break
            elif isinstance(t_entry, int):
                t_indices.add(t_entry)
            elif isinstance(t_entry, list):
                for t in t_entry:
                    t_indices.add(t)
            elif isinstance(t_entry, tuple):
                for t in range(t_entry[0],t_entry[1]):
                    t_indices.add(t)
            else:
                raise ValueError(f'Did not understand entry {t_entry} in config specified positions')

        self.num_t = len(t_indices)
        self.num_p = len(p_indices)
        self.p_indices = p_indices

        # create set of all possible pos, time indices
        for pos in p_indices:
            for time_point in t_indices:
                self.pt_set.add((pos, time_point))

    def _try_init_array(self, pt):
        try:

            # If not doing the full position, we still want semantic information on which positions
            # were reconstructed, so append the filename to the position (which would have otherwise been arbitrary)
            if not self.hcs_meta:
                name = f'Pos_{pt[0]:03d}'
            else:
                name = None

            self.pipeline.writer.init_array(self.indices_map[pt[0]],
                                            self.pipeline.data_shape,
                                            self.pipeline.chunk_size,
                                            self.pipeline.output_channels,
                                            position_name=name)

        # assumes array exists already if there is an error thrown
        except:
            pass

    #TODO: use arbol print statements
    #TODO: Refactor Birefringence to Anisotropy
    def run(self):

        print(f'Beginning Reconstruction...')

        for pt in sorted(self.pt_set):
            start_time = time.time()

            self._try_init_array(pt)

            pt_data = self.data.get_zarr(pt[0])[pt[1]] # (C, Z, Y, X) virtual

            # will return pt_data if the pipeline does not compute stokes
            stokes = self.pipeline.reconstruct_stokes_volume(pt_data)

            stokes = self.pre_processing(stokes)

            # will return None if the pipeline doesn't support birefringence reconstruction
            birefringence = self.pipeline.reconstruct_birefringence_volume(stokes)

            # will return either phase or fluorescent deconvolved volumes
            deconvolve2D, deconvolve3D = self.pipeline.deconvolve_volume(stokes)

            birefringence, deconvolve2D, deconvolve3D, registered_data = self.post_processing(pt_data,
                                                                                              deconvolve2D,
                                                                                              deconvolve3D,
                                                                                              birefringence)

            self.pipeline.write_data(self.indices_map[pt[0]], pt[1], pt_data, stokes,
                                     birefringence, deconvolve2D, deconvolve3D, registered_data)

            end_time = time.time()
            print(f'Finishing Reconstructing P = {pt[0]}, T = {pt[1]} ({(end_time-start_time)/60:0.2f}) min')

            existing_meta = self.writer.store.attrs.asdict().copy()
            existing_meta['Config'] = self.config.yaml_dict
            self.writer.store.attrs.put(existing_meta)

    def pre_processing(self, stokes):

        denoise_params = self._get_preprocessing_params()

        return preproc_denoise(stokes, denoise_params) if denoise_params else stokes

    def post_processing(self, pt_data, deconvolve2D, deconvolve3D, birefringence):

        denoise_params, registration_params = self._get_postprocessing_params()

        deconvolve2D_denoise = np.copy(deconvolve2D)
        deconvolve3D_denoise = np.copy(deconvolve3D)
        birefringence_denoise = np.copy(birefringence)
        if denoise_params:
            for chan_param in denoise_params:
                if 'Retardance' in chan_param[0]:
                    birefringence_denoise[0] = post_proc_denoise(birefringence[0], chan_param)
                elif 'Orientation' in chan_param[0]:
                    birefringence_denoise[1] = post_proc_denoise(birefringence[1], chan_param)
                elif 'Brightfield' in chan_param[0]:
                    birefringence_denoise[2] = post_proc_denoise(birefringence[2], chan_param)
                elif 'Phase2D' in chan_param[0]:
                    deconvolve2D_denoise = post_proc_denoise(deconvolve2D, chan_param)
                elif 'Phase3D' in chan_param[0]:
                    deconvolve3D_denoise = post_proc_denoise(deconvolve3D, chan_param)
                else:
                    raise ValueError(f'Didnt understand post_proc denoise channel {chan_param[0]}')

        if registration_params:
            registered_stacks = []

            if isinstance(self.pipeline, FluorescenceDeconvolution):

                for param in registration_params:

                    # NOTE: data can be registered/written in the wrong order if the user does not
                    # specify the processed channel indexes first in the config file.  In other words,
                    # registered_stacks doesn't keep track of which stacks sit where in the list, it is
                    # dependent on the list of indices given in pre_processing config

                    # need to account for the user wanting to register a deconvolved volume
                    if param[0] in self.pipeline.map:
                        loc = self.pipeline.map[param[0]]
                        if self.pipeline.mode == '3D':
                            registered_stacks.append(translate_3D(deconvolve3D[loc], param[1]))
                        elif self.pipeline.mode == '2D':
                            registered_stacks.append(translate_3D(deconvolve2D[loc], param[1]))
                        else:
                            raise ValueError('Failed to find deconvolved stack to register.')

                    # this accounts for a user wanting to register a non-processed dataset
                    else:
                        registered_stacks.append(translate_3D(pt_data[param[0]], param[1]))
            else:
                for param in registration_params:

                    registered_stacks.append(translate_3D(pt_data[param[0]], param[1]))

        else:
            registered_stacks = None

        return birefringence_denoise, deconvolve2D_denoise, deconvolve3D_denoise, registered_stacks
