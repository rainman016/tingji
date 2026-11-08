"""Offline English recognition, followed by bounded speaker clustering."""
from contextlib import contextmanager
import gc
import math
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
_DLL_HANDLES = []


def phrase_segments(part, duration_ms):
    """Keep every word, with original timestamps and punctuation; bound phrase length."""
    groups, current = [], []
    for word in part.words or []:
        current.append(word)
        elapsed = current[-1].end - current[0].start
        if elapsed >= 10 or (elapsed >= .9 and word.word.rstrip().endswith((".", "?", "!"))):
            groups.append((current[0].start, current[-1].end, "".join(w.word for w in current)))
            current = []
    if current:
        groups.append((current[0].start, current[-1].end, "".join(w.word for w in current)))
    if not groups:
        groups = [(part.start, part.end, part.text)]
    segments = []
    for start, end, text in groups:
        if not all(math.isfinite(value) for value in (start, end)):
            raise ValueError("英文模型返回了无效时间戳，请重试。")
        start_ms, end_ms = max(0, round(start * 1000)), min(duration_ms, round(end * 1000))
        if text.strip() and end_ms > start_ms:
            segments.append({"start_ms": start_ms, "end_ms": end_ms, "text": text.strip(), "speaker": "unknown"})
    return segments


def cluster_labels(embeddings, speaker_count=0):
    """Cluster at most 512 representative samples, then map every segment globally."""
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering, KMeans
    from funasr.models.campplus.cluster_backend import ClusterBackend
    vectors = np.asarray(embeddings, dtype="float32")
    if not np.isfinite(vectors).all():
        raise ValueError("说话人特征无效，请确认录音中有清晰人声。")
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
    if len(vectors) <= 1 or speaker_count == 1:
        return np.zeros(len(vectors), dtype=int)
    indexes = np.linspace(0, len(vectors)-1, min(512, len(vectors)), dtype=int)
    sample = vectors[indexes]
    count = min(speaker_count, len(sample)) if speaker_count else None
    backend = ClusterBackend(max_num_spks=10)
    if count:
        labels = KMeans(n_clusters=count, n_init=10, random_state=0).fit_predict(sample)
    elif len(sample) < 20:
        labels = AgglomerativeClustering(n_clusters=None, distance_threshold=.22,
                                        metric="cosine", linkage="average").fit_predict(sample)
        labels = backend.merge_to_max_spks(labels, sample, 10)
    else:
        labels = backend(sample, oracle_num=None)
    identities = np.unique(labels)
    centers = np.stack([sample[labels == identity].mean(axis=0) for identity in identities])
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-8)
    assigned = identities[(vectors @ centers.T).argmax(axis=1)]
    # Keep the clustering decisions for the sampled observations themselves.
    assigned[indexes] = labels
    return assigned


def identify_speakers(audio_path, segments, count, device, progress, *, store=None):
    if count == 1 or len(segments) == 1:
        labels = [0] * len(segments)
    else:
        import torch
        from resumable_transcription import MemoryStore, speaker_embeddings

        @contextmanager
        def encoder_factory():
            from funasr import AutoModel
            progress("文字已识别，正在加载说话人模型…")
            encoder = AutoModel(model=str(ROOT / "models" / "campplus"), device=device,
                                ncpu=6, disable_update=True, trust_remote_code=False,
                                disable_pbar=True, disable_log=True)
            def encode(clip):
                result = encoder.generate(input=clip, fs=16000, disable_pbar=True)
                return result[0]["spk_embedding"].detach().cpu().numpy().reshape(-1).tolist()
            try:
                with torch.inference_mode():
                    yield encode
            finally:
                del encoder
                gc.collect()
                if device == "cuda":
                    torch.cuda.empty_cache()

        embeddings = speaker_embeddings(audio_path, segments, store if store is not None else MemoryStore(),
                                        progress, encoder_factory)
        progress("正在汇总全片的说话人分组…")
        labels = cluster_labels(embeddings, count)
    mapping, speakers = {}, {}
    for segment, identity in zip(segments, labels):
        identity = int(identity)
        if identity not in mapping:
            key = str(len(mapping))
            mapping[identity] = key
            speakers[key] = f"说话人 {len(speakers)+1}"
        segment["speaker"] = mapping[identity]
    return speakers


def transcribe_english(request, progress):
    import torch
    from resumable_transcription import transcribe_chunks, worker_store
    torch.set_num_threads(6)
    # Reuse the CUDA/cuDNN libraries already installed in this project's PyTorch.
    library_dir = Path(torch.__file__).parent / "lib"
    if os.name == "nt" and library_dir.is_dir():
        os.environ["PATH"] = str(library_dir) + os.pathsep + os.environ.get("PATH", "")
        _DLL_HANDLES.append(os.add_dll_directory(str(library_dir)))
    device = "cuda" if request.get("device") != "cpu" and torch.cuda.is_available() else "cpu"
    started = time.monotonic()
    store = worker_store(request)

    @contextmanager
    def recognizer_factory():
        from faster_whisper import WhisperModel
        progress("正在加载英文识别模型（" + ("显卡" if device == "cuda" else "CPU") + "）…")
        model = WhisperModel(str(ROOT / "models" / "whisper-small-en"), device=device,
                             compute_type="float16" if device == "cuda" else "int8",
                             cpu_threads=6, num_workers=1, local_files_only=True)
        def recognize(clip, duration_ms):
            generated, _ = model.transcribe(clip, language="en", task="transcribe", beam_size=5,
                                            word_timestamps=True, vad_filter=True,
                                            vad_parameters={"min_silence_duration_ms": 500},
                                            condition_on_previous_text=False)
            rows = []
            for part in generated:
                rows.extend(phrase_segments(part, duration_ms))
            return rows
        try:
            yield recognize
        finally:
            del model
            gc.collect()
            if device == "cuda":
                torch.cuda.empty_cache()

    segments = transcribe_chunks(request["audio"], store, progress, recognizer_factory, "英文")
    speakers = identify_speakers(request["audio"], segments, request.get("speaker_count", 0),
                                device, progress, store=store)
    return {"segments": segments, "speakers": speakers, "device": device,
            "seconds": round(time.monotonic()-started, 2), "language": "en"}
