#!/usr/bin/env python3
import argparse
import subprocess
import threading
import time
import collections
import io
import logging
import sys
import warnings
from typing import Optional
from PIL import Image

# Suppress Pillow deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("LoopVerifier")

try:
    import imagehash
    # Confirm it's actually working (sometimes pyenv/pip issues make it an empty module)
    _ = imagehash.phash(Image.new('RGB', (10, 10)))
except Exception:
    logger.warning("imagehash NOT functional. Falling back to simple Mean Hash.")
    imagehash = None

# --- Core SidecarLoopDetector logic (standalone) ---

SEQUENCE_LENGTH = 3
HAMMING_TOLERANCE = 5
# Tighten tolerance for simple hash to avoid false positives in synthetic tests
if 'imagehash' not in sys.modules or sys.modules['imagehash'] is None:
    HAMMING_TOLERANCE = 2

LOOP_DURATION_THRESHOLD = 10.0
BUFFER_MAXLEN = 600
STALE_THRESHOLD = 30.0

class SidecarLoopDetector:
    def __init__(self, pipe, clock=time.monotonic):
        self.pipe = pipe
        self.clock = clock
        self.buffer = collections.deque(maxlen=BUFFER_MAXLEN)
        self.last_frame_time = 0.0
        self.is_closed = False
        self.detection_event = threading.Event()
        self.detected_duration = None
        
    def run(self):
        logger.info("Detector worker started")
        try:
            while not self.is_closed:
                frame_data = self._read_ppm_frame()
                if not frame_data:
                    break
                
                timestamp = self.clock()
                self.last_frame_time = timestamp
                
                try:
                    img = Image.open(io.BytesIO(frame_data))
                    if imagehash:
                        h = imagehash.phash(img)
                    else:
                        h = self._simple_hash(img)
                    
                    logger.debug(f"Frame Hash: {h}")
                    self.buffer.append((timestamp, h)) # Store (timestamp, hash)
                    
                    duration = self.detect_loop()
                    if duration:
                        logger.warning(f"!!! LOOP DETECTED: {duration:.2f}s !!!")
                        self.detected_duration = duration
                        self.detection_event.set()
                        
                except Exception as e:
                    logger.error(f"Error processing frame: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Detector unexpected error: {e}")
        finally:
            self.is_closed = True
            logger.info("Detector worker stopped")

    def _read_ppm_frame(self) -> Optional[bytes]:
        """Reads one P6 PPM frame from pipe."""
        try:
            header = b""
            # We need 3 lines: P6, dimensions, maxval
            lines_collected = 0
            while lines_collected < 3:
                char = self.pipe.read(1)
                if not char:
                    return None
                header += char
                if char == b"\n":
                    lines_collected += 1
            
            # Parse header
            lines = header.decode().strip().split("\n")
            if lines[0] != "P6":
                return None
            
            width, height = map(int, lines[1].split())
            # max_val = int(lines[2]) # usually 255
            
            data_size = width * height * 3
            data = b""
            while len(data) < data_size:
                chunk = self.pipe.read(data_size - len(data))
                if not chunk:
                    return None
                data += chunk
            
            return header + data
        except Exception as e:
            logger.debug(f"PPM Read Error: {e}")
            return None
    def _hamming_dist(self, h1, h2) -> int:
        if hasattr(h1, '__sub__'): # ImageHash object
            return h1 - h2
        # Integer fallback
        return bin(h1 ^ h2).count('1')

    def detect_loop(self) -> Optional[float]:
        if len(self.buffer) < SEQUENCE_LENGTH * 2:
            return None
            
        # Get 3 most recent hashes: h_2, h_1, h0 (where h0 is the newest)
        recent = list(self.buffer)[-SEQUENCE_LENGTH:]
        h_2, h_1, h0 = [item[1] for item in recent]
        t0 = recent[-1][0]
        
        # Static filter
        if (self._hamming_dist(h0, h_1) <= HAMMING_TOLERANCE) and \
           (self._hamming_dist(h_1, h_2) <= HAMMING_TOLERANCE):
            return None

        history = list(self.buffer)[:-SEQUENCE_LENGTH]
        for i in range(len(history) - SEQUENCE_LENGTH + 1):
            match_t2 = self._hamming_dist(history[i][1], h_2) <= HAMMING_TOLERANCE
            match_t1 = self._hamming_dist(history[i+1][1], h_1) <= HAMMING_TOLERANCE
            match_t0 = self._hamming_dist(history[i+2][1], h0) <= HAMMING_TOLERANCE
            
            if match_t2 and match_t1 and match_t0:
                t_match = history[i+2][0]
                duration = t0 - t_match
                if duration >= LOOP_DURATION_THRESHOLD:
                    return duration
        return None

    def _simple_hash(self, img):
        img = img.convert('L').resize((8, 8), Image.Resampling.LANCZOS)
        pixels = img.get_flattened_data()
        avg = sum(pixels) / 64
        return sum(1 << i for i, v in enumerate(pixels) if v > avg)

# --- Synthetic Stream Generation ---

def run_test(loop_seconds: float, total_seconds: int, mode: str):
    """
    Modes:
    - 'loop': Replay the input in a loop of loop_seconds
    - 'normal': Just play normally
    - 'static': Static image
    """
    logger.info(f"Starting test: mode={mode}, loop_seconds={loop_seconds}, total_seconds={total_seconds}")
    
    # We use a test pattern if no input file is provided
    ffmpeg_input = "-f lavfi -i testsrc=size=640x360:rate=30"
    
    if mode == 'loop':
        num_loops = int(total_seconds / loop_seconds) + 2
        ffmpeg_cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'info',
            '-f', 'lavfi', '-i', f'testsrc2=size=320x240:rate=1:duration={loop_seconds}',
            '-vf', f"loop=loop={num_loops}:size=1000:start=0,scale=32:32,format=gray",
            '-t', str(total_seconds),
            '-c:v', 'ppm', '-f', 'image2pipe', 'pipe:1'
        ]
    elif mode == 'static':
        # Use a truly static pattern: color bar from smptebars, no animation
        ffmpeg_cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'info',
            '-f', 'lavfi', '-i', 'smptebars=size=320x240:rate=1',
            '-vf', 'scale=32:32,format=gray',
            '-t', str(total_seconds),
            '-c:v', 'ppm', '-f', 'image2pipe', 'pipe:1'
        ]
    else: # normal
        # Use noise to ensure no periodic matches
        ffmpeg_cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'info',
            '-f', 'lavfi', '-i', 'noise=alls_seed=123:alls_f=t,scale=32:32,format=gray',
            '-t', str(total_seconds),
            '-c:v', 'ppm', '-f', 'image2pipe', 'pipe:1'
        ]

    proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    def log_ffmpeg_stderr(p):
        for line in p.stderr:
            logger.info(f"FFmpeg Stderr: {line.decode().strip()}")
            
    stderr_thread = threading.Thread(target=log_ffmpeg_stderr, args=(proc,), daemon=True)
    stderr_thread.start()

    # Simulated clock for fast synthetic tests
    sim_time = 0.0
    def get_sim_time():
        nonlocal sim_time
        return sim_time
    
    def ticking_clock():
        nonlocal sim_time
        t = sim_time
        sim_time += 1.0 # 1fps
        return t

    detector = SidecarLoopDetector(proc.stdout, clock=ticking_clock)
    
    thread = threading.Thread(target=detector.run, daemon=True)
    thread.start()
    
    start_time = time.time()
    success = False
    
    try:
        while get_sim_time() < total_seconds:
            if detector.detection_event.wait(timeout=0.01):
                if mode == 'loop':
                    logger.info(f"Success: Detected loop of {detector.detected_duration:.2f}s (expected ~{loop_seconds}s)")
                    success = True
                    break
                else:
                    logger.error(f"Failure: Detected loop in '{mode}' mode!")
                    success = False # Explicit failure
                    break
            
            if proc.poll() is not None:
                break
        
        # FINAL WAIT: After FFmpeg finishes, the detector thread might still have frames in its queue
        if not success:
            logger.debug("Waiting for detector thread to drain remaining frames...")
            thread.join(timeout=2.0)
            if detector.detection_event.is_set():
                if mode == 'loop':
                    logger.info(f"Success: Detected loop of {detector.detected_duration:.2f}s (late detection)")
                    success = True
                else:
                    logger.error(f"Failure: Late loop detection in '{mode}' mode!")
                    success = False
            else:
                if mode == 'loop':
                    logger.error("Failure: No loop detected in 'loop' mode")
                    success = False
                else:
                    logger.info(f"Success: No loop detected in '{mode}' mode")
                    success = True
            
    finally:
        detector.is_closed = True
        proc.terminate()
        
    return success

def main():
    parser = argparse.ArgumentParser(description="Verify Stream Loop Detection")
    parser.add_argument("--mode", choices=['loop', 'normal', 'static'], default='loop', help="Test mode")
    parser.add_argument("--loop-duration", type=float, default=10.0, help="Duration of the loop in seconds")
    parser.add_argument("--total-duration", type=int, default=30, help="Total duration of the test in seconds")
    parser.add_argument("--loglevel", default="INFO", help="Logging level (DEBUG, INFO, etc.)")
    
    args = parser.parse_args()
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.loglevel.upper()))
    
    success = run_test(args.loop_duration, args.total_duration, args.mode)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
