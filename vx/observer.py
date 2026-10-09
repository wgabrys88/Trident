import sys, time
from bus import Config
class Observer:
    def follow(self, path):
        with Config().path(path).open(encoding='utf-8') as journal:
            while True:
                position, line = journal.tell(), journal.readline()
                if line.endswith('\n'): print(line, end='', flush=True)
                else: journal.seek(position); time.sleep(Config()['bus']['poll'])
Observer().follow(sys.argv[1] + '/bus.log')
