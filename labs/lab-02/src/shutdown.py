import signal


class StopFlag:
    def __init__(self):
        self.requested = False

    def install(self, message):
        def handler(signum, frame):
            self.requested = True
            print(f'\n[!] {message}')
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        return self

    def __bool__(self):
        return self.requested
