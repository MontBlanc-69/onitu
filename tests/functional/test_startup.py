from os import unlink

from tests.utils.launcher import Launcher
from tests.utils.setup import Setup
from tests.utils.driver import LocalStorageDriver, TargetDriver
from tests.utils.loop import CounterLoop

launcher = None
rep1, rep2 = LocalStorageDriver('rep1'), TargetDriver('rep2')
json_file = 'test_startup.json'


def setup_module(module):
    global launcher
    setup = Setup()
    setup.add(*rep1.setup)
    setup.add(*rep2.setup)
    setup.save(json_file)
    launcher = Launcher(json_file)


def teardown_module(module):
    launcher.kill()
    unlink(json_file)
    rep1.close()
    rep2.close()


def test_all_active():
    loop = CounterLoop(3)

    launcher.on_referee_started(loop.check)
    launcher.on_driver_started(loop.check, driver='rep1')
    launcher.on_driver_started(loop.check, driver='rep2')
    launcher()

    loop.run(timeout=5)

    launcher.quit()
