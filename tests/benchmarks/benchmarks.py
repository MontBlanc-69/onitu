from os import unlink
from sys import argv

from tests.utils.launcher import Launcher
from tests.utils.setup import Setup
from tests.utils.loop import BooleanLoop, CounterLoop
from tests.utils.driver import LocalStorageDriver
from tests.utils.benchmark import Benchmark, BenchmarkData
from tests.utils.timer import Timer

SMALL = 1024 * 1024
MEDIUM = SMALL * 10
BIG = MEDIUM * 10


class BenchmarkSimpleCopy(Benchmark):
    def launch_onitu(self):
        self.launcher = None
        self.json_file = 'bench_copy.json'
        self.rep1 = LocalStorageDriver('rep1')
        self.rep2 = LocalStorageDriver('rep2')
        setup = Setup()
        setup.add(self.rep1)
        setup.add(self.rep2)
        setup.save(self.json_file)
        loop = CounterLoop(3)
        self.launcher = Launcher(self.json_file)
        self.launcher.on_referee_started(loop.check)
        self.launcher.on_driver_started(loop.check, driver='rep1')
        self.launcher.on_driver_started(loop.check, driver='rep2')
        self.launcher()
        loop.run(timeout=5)

    def stop_onitu(self):
        self.launcher.kill()
        unlink(self.json_file)
        self.rep1.close()
        self.rep2.close()

    def setup(self):
        self.launch_onitu()

    def teardown(self):
        self.stop_onitu()

    def copy_file(self, filename, size, timeout=10):
        self.launcher.unset_all_events()
        loop = BooleanLoop()
        self.launcher.on_transfer_ended(
            loop.stop, d_from='rep1', d_to='rep2', filename=filename
        )
        self.rep1.generate(filename, size)
        with Timer() as t:
            loop.run(timeout=timeout)
        assert (self.rep1.checksum(filename) == self.rep2.checksum(filename))
        return t.msecs

    def test_small(self):
        total = BenchmarkData('test_small', 'Copy 1000 times a 1M file')
        for i in range(1000):
            try:
                t = self.copy_file('small{}'.format(i), SMALL)
                total.add_result(t)
            except BaseException as e:
                self._log('Error in test_small')
                self._log(e)
        return total

    def test_medium(self):
        total = BenchmarkData('test_medium', 'Copy 100 times a 10M file')
        for i in range(100):
            try:
                t = self.copy_file('medium{}'.format(i), MEDIUM)
                total.add_result(t)
            except BaseException as e:
                self._log('Error in test_medium')
                self._log(e)
        return total

    def test_big(self):
        total = BenchmarkData('test_big', 'Copy 10 times a 100M file')
        for i in range(10):
            try:
                t = self.copy_file('big{}'.format(i), BIG)
                total.add_result(t)
            except BaseException as e:
                self._log('Error in test_big')
                self._log(e)
        return total


class BenchmarkMultipleCopies(Benchmark):
    def launch_onitu(self):
        self.launcher = None
        self.json_file = 'bench_multiple_copy.json'
        self.rep1 = LocalStorageDriver('rep1')
        self.rep2 = LocalStorageDriver('rep2')
        self.rep3 = LocalStorageDriver('rep3')
        setup = Setup()
        setup.add(self.rep1)
        setup.add(self.rep2)
        setup.add(self.rep3)
        setup.save(self.json_file)
        loop = CounterLoop(4)
        self.launcher = Launcher(self.json_file)
        self.launcher.on_referee_started(loop.check)
        self.launcher.on_driver_started(loop.check, driver='rep1')
        self.launcher.on_driver_started(loop.check, driver='rep2')
        self.launcher.on_driver_started(loop.check, driver='rep3')
        self.launcher()
        loop.run(timeout=5)

    def stop_onitu(self):
        self.launcher.kill()
        unlink(self.json_file)
        self.rep1.close()
        self.rep2.close()
        self.rep3.close()

    def setup(self):
        self.launch_onitu()

    def teardown(self):
        self.stop_onitu()

    def copy_file(self, filename, size, timeout=20):
        self.launcher.unset_all_events()
        loop = BooleanLoop()
        loop = CounterLoop(2)
        self.launcher.on_transfer_ended(
            loop.check, d_from='rep1', d_to='rep2', filename=filename
        )
        self.launcher.on_transfer_ended(
            loop.check, d_from='rep1', d_to='rep3', filename=filename
        )
        self.rep1.generate(filename, size)
        with Timer() as t:
            loop.run(timeout=timeout)
        assert self.rep1.checksum(filename) == self.rep2.checksum(filename)
        assert self.rep1.checksum(filename) == self.rep3.checksum(filename)
        return t.msecs

    def test_small(self):
        total = BenchmarkData('test_small', 'Copy 1000 times a 1M file')
        for i in range(1000):
            try:
                t = self.copy_file('small', SMALL)
                total.add_result(t)
            except BaseException as e:
                self._log('Error in test_small')
                self._log(e)
        return total

    def test_medium(self):
        total = BenchmarkData('test_medium', 'Copy 100 times a 10M file')
        for i in range(100):
            try:
                t = self.copy_file('medium', MEDIUM)
                total.add_result(t)
            except BaseException as e:
                self._log('Error in test_medium')
                self._log(e)
        return total

    def test_big(self):
        total = BenchmarkData('test_big', 'Copy 10 times a 100M file')
        for i in range(10):
            try:
                t = self.copy_file('big', BIG)
                total.add_result(t)
            except BaseException as e:
                self._log('Error in test_big')
                self._log(e)
        return total

if __name__ == '__main__':
    bench_simple = BenchmarkSimpleCopy(verbose=True)
    bench_simple.run()
    bench_multiple = BenchmarkMultipleCopies(verbose=True)
    bench_multiple.run()
    print('{:=^28}'.format(' simple copy '))
    bench_simple.display()
    print('{:=^28}'.format(' multiple copy '))
    bench_multiple.display()
    # TODO: clean this shit
    if len(argv) >= 7 and argv[1] in ('-u', '--upload'):
        host = argv[2]
        environment = argv[3]
        project = argv[4]
        commitid = argv[5]
        branch = argv[6]
        bench_simple.upload_results(
            'copy single destination',
            host,
            environment,
            project,
            commitid,
            branch
        )
        bench_multiple.upload_results(
            'copy mutiple destinations',
            host,
            environment,
            project,
            commitid,
            branch
        )