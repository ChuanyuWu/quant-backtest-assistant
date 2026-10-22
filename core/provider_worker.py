"""Isolated AkShare calls: hard deadlines also cover APIs without timeout arguments."""
import multiprocessing as mp


def _worker(pipe, operation, kwargs):
    try:
        import akshare
        pipe.send((True, getattr(akshare, operation)(**kwargs)))
    except Exception as exc:
        pipe.send((False, str(exc)))
    finally:
        pipe.close()


def call(operation, timeout=40, **kwargs):
    context = mp.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(writer, operation, kwargs), daemon=True)
    process.start()
    writer.close()
    try:
        if not reader.poll(timeout):
            raise TimeoutError(f"{operation} 请求超时（{timeout}秒）")
        ok, payload = reader.recv()
        if not ok:
            raise RuntimeError(payload)
        return payload
    finally:
        reader.close()
        if process.is_alive():
            process.terminate()
        process.join()
