import click
import shutil
import os
from recOrder.io.config_reader import ConfigReader
from recOrder.pipelines.pipeline_constructor import PipelineConstructor

@click.command()
@click.option('--mode', required=True, type=str, help='mode of reconstruction: \
                                                      QLIPP_3D,QLIPP_2D,stokes')
@click.option('--data_dir', required=True, type=str, help='path to the data')
@click.option('--save_dir', required=True, type=str, help='path to the save directory')
@click.option('--name', required=True, type=str, help='name to use for saving the data')
@click.option('--config', required=False, type=str, help='path to config yml file')
def parse_args(mode, data_dir, save_dir, name, config):
    """parse command line arguments and return class with the arguments"""

    class Args():
        def __init__(self, mode, data_dir, save_dir, name, config):
            self.mode = mode
            self.config = config
            self.data_dir = data_dir
            self.save_dir = save_dir
            self.name = name

    return Args(mode, data_dir, save_dir, name, config)

if __name__ == '__main__':

    Args = parse_args(standalone_mode=False)
    print(Args.config, Args.name, Args.save_dir, Args.data_dir)

    if Args.config:
        if not os.path.exists(Args.config):
            raise ValueError('Specified config path does not exist')
        else:
            config = ConfigReader(Args.config)
    else:
        config = ConfigReader()

    constructor = PipelineConstructor(Args.mode, Args.data_dir, Args.save_dir, Args.name, config)
    constructor.run()
    shutil.copy(Args.config, os.path.join(Args.save_dir, 'config.yml'))
