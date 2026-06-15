"""
sync_manager.py — Background connectivity monitor and Firebase retry engine.

Checks internet connectivity every 30 seconds.
When the machine comes back online after being offline, all queued
Firebase sync operations are retried automatically.
"""
import threading
import time
import queue
import logging

_online       = False
_retry_q      = queue.Queue(maxsize=2000)
_online_cbs   = []          # called once when connectivity is restored
_lock         = threading.Lock()
_started      = False

log = logging.getLogger('sync_manager')


def online() -> bool:
    """True if the last connectivity check succeeded."""
    return _online


def register_online_callback(fn):
    """Register a function to be called when connectivity is restored."""
    _online_cbs.append(fn)


def queue_retry(fn, *args):
    """Queue a Firebase sync call for retry when back online."""
    try:
        _retry_q.put_nowait((fn, args))
    except queue.Full:
        pass


def _check_net() -> bool:
    """Try to reach Firebase's RTDB host. Returns True if reachable."""
    try:
        import socket
        socket.setdefaulttimeout(3)
        socket.getaddrinfo('firebaseio.com', 443)
        return True
    except Exception:
        return False


def _flush_retry_queue():
    """Drain and execute all queued sync operations."""
    flushed = 0
    while not _retry_q.empty():
        try:
            fn, args = _retry_q.get_nowait()
            fn(*args)
            flushed += 1
        except queue.Empty:
            break
        except Exception as e:
            log.warning('Retry sync failed: %s', e)
    if flushed:
        log.info('Flushed %d queued sync operations.', flushed)
    return flushed


def _run():
    global _online
    prev = _check_net()
    _online = prev

    while True:
        time.sleep(30)
        now = _check_net()

        with _lock:
            if now and not prev:
                # Just came back online
                _online = True
                log.info('Network restored — flushing sync queue.')
                for cb in _online_cbs:
                    try:
                        cb()
                    except Exception as e:
                        log.warning('Online callback error: %s', e)
                _flush_retry_queue()
            elif not now:
                _online = False
            prev = now


def start():
    """Start the background monitor. Safe to call multiple times."""
    global _started, _online
    with _lock:
        if _started:
            return
        _started = True

    _online = _check_net()
    t = threading.Thread(target=_run, daemon=True, name='SyncMonitor')
    t.start()
    log.info('Sync manager started — online=%s', _online)
