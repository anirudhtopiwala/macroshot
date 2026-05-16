import { useEffect, useRef, useState, useCallback } from 'react';
import * as Sentry from '@sentry/react';
import { BarcodeDetectorPolyfill } from '@undecaf/barcode-detector-polyfill';
import { X, Zap } from './icons';
import LoadingSpinner from './LoadingSpinner';
import useOverlayHistory from '../hooks/useOverlayHistory';
import { hapticSuccess, hapticLight, hapticWarning } from '../utils/haptics';
import { trackEvent } from '../utils/analytics';

type CameraErrorType = 'denied' | 'not_found' | 'in_use' | 'no_api' | 'other';

interface Props {
  onScan: (barcode: string) => void;
  onClose: () => void;
  onSwitchPhoto?: () => void;
  onSwitchText?: () => void;
  onManualEntry?: () => void;
  /** Called when a QR code with a non-barcode payload is detected (e.g. a URL).
   *  Digit-only QR payloads (8–14 digits) still route through onScan so they
   *  reuse the barcode cache + flow. */
  onQrScan?: (payload: string) => void;
}

/** Detect platform for permission-recovery instructions. */
function getPermissionInstructions(): { platform: string; steps: string[] } {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : '';
  const isIOS = /iPhone|iPad|iPod/.test(ua);
  const isAndroid = /Android/.test(ua);
  const isChrome = /Chrome|CriOS/.test(ua) && !/Edge|Edg\//.test(ua);
  const isSafari = /Safari/.test(ua) && !/Chrome|CriOS|Android/.test(ua);
  const isStandalone = typeof window !== 'undefined'
    && (window.matchMedia?.('(display-mode: standalone)').matches
      || (navigator as unknown as { standalone?: boolean }).standalone === true);

  if (isIOS && isStandalone) {
    return {
      platform: 'iOS installed app',
      steps: [
        'Open the iOS Settings app',
        'Scroll down and tap MacroShot',
        'Toggle Camera on',
        'Return here and tap Try Again',
      ],
    };
  }
  if (isIOS && isSafari) {
    return {
      platform: 'iOS Safari',
      steps: [
        'Tap the "aA" button on the left of the address bar',
        'Tap "Website Settings"',
        'Set Camera to "Allow"',
        'Return here and tap Try Again',
      ],
    };
  }
  if (isIOS) {
    return {
      platform: 'iOS',
      steps: [
        'Open iOS Settings → Safari → Camera',
        'Set to "Ask" or "Allow"',
        'Reload this page',
      ],
    };
  }
  if (isAndroid && isChrome) {
    return {
      platform: 'Android Chrome',
      steps: [
        'Tap the lock (or "tune") icon next to the URL',
        'Tap Permissions / Site settings → Camera',
        'Choose Allow',
        'Reload this page',
      ],
    };
  }
  if (isChrome) {
    return {
      platform: 'Desktop Chrome',
      steps: [
        'Click the camera icon on the right of the address bar',
        'Select "Always allow" for this site',
        'Reload this page',
      ],
    };
  }
  if (isSafari) {
    return {
      platform: 'Desktop Safari',
      steps: [
        'Open Safari → Settings → Websites → Camera',
        'Find this site and set to "Allow"',
        'Reload this page',
      ],
    };
  }
  return {
    platform: 'Your browser',
    steps: [
      'Open your browser settings',
      'Find site permissions for this page',
      'Allow Camera access',
      'Reload this page',
    ],
  };
}

export default function BarcodeScanner({ onScan, onClose, onSwitchPhoto, onSwitchText, onManualEntry, onQrScan }: Props) {
  useOverlayHistory(onClose);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number>(0);
  const mountedRef = useRef(true);
  const lastScanRef = useRef<{ code: string; time: number }>({ code: '', time: 0 });
  const flashTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dupTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastDetectTimeRef = useRef<number>(0);
  const consecutiveFailsRef = useRef<number>(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [flash, setFlash] = useState<'green' | 'yellow' | false>(false);
  const [dupText, setDupText] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [cameraErrorType, setCameraErrorType] = useState<CameraErrorType | null>(null);
  const [fileDetecting, setFileDetecting] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [showInstructions, setShowInstructions] = useState(false);
  const [torchAvailable, setTorchAvailable] = useState(false);
  const [torchOn, setTorchOn] = useState(false);
  const [debugInfo, setDebugInfo] = useState('Initializing...');
  const [timedOut, setTimedOut] = useState(false);

  const [nonFoodBarcode, setNonFoodBarcode] = useState<{ code: string; format: string } | null>(null);
  const [scanPhase, setScanPhase] = useState<'scanning' | 'slow' | 'warning'>('scanning');
  const [cameraKey, setCameraKey] = useState(0); // increment to restart camera (e.g. after non-food barcode rescan)
  const scanTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scanSlowRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scanWarningRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onScanRef = useRef(onScan);
  onScanRef.current = onScan;
  const onQrScanRef = useRef(onQrScan);
  onQrScanRef.current = onQrScan;

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    // Prevent iOS memory leaks: release video source and clear canvas
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.srcObject = null;
    }
    if (canvasRef.current) {
      const ctx = canvasRef.current.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
    }
  }, []);

  const toggleTorch = useCallback(async () => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    const next = !torchOn;
    try {
      await (track as any).applyConstraints({ advanced: [{ torch: next }] });
      setTorchOn(next);
    } catch { /* Torch not supported */ }
  }, [torchOn]);

  // Retry getUserMedia. Works if the user has fixed the permission in
  // browser settings since they landed here. On still-denied browsers this
  // just fails silently and the error UI stays put.
  const handleTryAgain = useCallback(() => {
    setCameraError(null);
    setCameraErrorType(null);
    setFileError(null);
    setShowInstructions(false);
    setTimedOut(false);
    setCameraKey(k => k + 1);
  }, []);

  // File-picker fallback: user snaps a photo of the barcode via the native
  // camera app. We run multi-pass detection on the resulting image so slightly
  // blurry, rotated, or off-angle shots still decode. Fires onScan on success.
  const handleFileCapture = useCallback(async (file: File) => {
    setFileDetecting(true);
    setFileError(null);

    try {
      // Load the picked file into an HTMLImageElement. Both FileReader and
      // img.onload can pend forever on corrupt/oversized files - cap each.
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        const to = setTimeout(() => reject(new Error('File read timeout')), 5000);
        reader.onload = () => { clearTimeout(to); resolve(reader.result as string); };
        reader.onerror = () => { clearTimeout(to); reject(reader.error); };
        reader.readAsDataURL(file);
      });
      const img = new Image();
      await new Promise<void>((resolve, reject) => {
        const to = setTimeout(() => reject(new Error('Image load timeout')), 5000);
        img.onload = () => { clearTimeout(to); resolve(); };
        img.onerror = () => { clearTimeout(to); reject(new Error('Could not load image')); };
        img.src = dataUrl;
      });

      let detector: BarcodeDetectorPolyfill;
      try {
        detector = new BarcodeDetectorPolyfill({
          formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'codabar'],
        });
      } catch (detErr) {
        console.error('BarcodeDetector init failed in file mode:', detErr);
        setFileError('Barcode scanner not available. Try entering the number manually.');
        setFileDetecting(false);
        return;
      }

      // Multi-pass: original, 90°, 180°, 270°. Return the first detected food
      // barcode (8-14 digits) or null.
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      if (!ctx) {
        setFileError('Could not process image.');
        setFileDetecting(false);
        return;
      }

      const tryDetect = async (rotation: 0 | 90 | 180 | 270): Promise<string | null> => {
        const rotated = rotation === 90 || rotation === 270;
        canvas.width = rotated ? img.height : img.width;
        canvas.height = rotated ? img.width : img.height;
        ctx.save();
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate((rotation * Math.PI) / 180);
        ctx.drawImage(img, -img.width / 2, -img.height / 2);
        ctx.restore();
        try {
          const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const results = await detector.detect(imageData);
          for (const r of results) {
            const code = r.rawValue;
            if (/^\d{8,14}$/.test(code)) return code;
          }
        } catch {
          // Per-rotation failures are expected when a barcode is only readable
          // at one orientation - the next rotation in the loop handles it.
          // If all four rotations fail, the caller captures to Sentry.
        }
        return null;
      };

      const rotations: Array<0 | 90 | 180 | 270> = [0, 90, 180, 270];
      for (const rot of rotations) {
        const code = await tryDetect(rot);
        if (code) {
          hapticSuccess();
          trackEvent('barcode_scanned_from_photo');
          onScanRef.current(code);
          return;
        }
      }

      // No passes succeeded
      hapticWarning();
      setFileError("Couldn't read a barcode in that photo. Make sure the barcode fills most of the frame and isn't blurry.");
    } catch (err) {
      console.error('File barcode detection failed:', err);
      if (Sentry.isInitialized()) {
        Sentry.captureException(err, { tags: { component: 'BarcodeScanner', stage: 'file-detect' } });
      }
      setFileError('Could not process the image. Please try again.');
    } finally {
      setFileDetecting(false);
    }
  }, []);

  const openFilePicker = useCallback(() => {
    setFileError(null);
    fileInputRef.current?.click();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    let detector: BarcodeDetectorPolyfill | null = null;

    try {
      detector = new BarcodeDetectorPolyfill({
        formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'codabar', 'qr_code'],
      });
      setDebugInfo('Detector ready, starting camera...');
    } catch (err) {
      console.error('BarcodeDetector init failed:', err);
      if (Sentry.isInitialized()) Sentry.captureException(err, { tags: { component: 'BarcodeScanner', stage: 'detector-init' } });
      setCameraError('Barcode scanner not available. Try updating your browser.');
      return;
    }

    let detectCount = 0;

    // Canvas-based detection: draw video frame to canvas, pass ImageData to detector
    // This approach works reliably on iOS Safari
    const detectLoop = async () => {
      if (!mountedRef.current) return;
      const video = videoRef.current;
      if (!video || !detector || video.readyState < 2) {
        rafRef.current = requestAnimationFrame(detectLoop);
        return;
      }

      const now = performance.now();
      if (now - lastDetectTimeRef.current < 50) { // ~20fps - fast barcode detection
        rafRef.current = requestAnimationFrame(detectLoop);
        return;
      }
      lastDetectTimeRef.current = now;
      detectCount++;

      try {
        // Draw video to offscreen canvas and extract ImageData
        if (!canvasRef.current) canvasRef.current = document.createElement('canvas');
        const canvas = canvasRef.current;
        const w = video.videoWidth;
        const h = video.videoHeight;
        if (w === 0 || h === 0) {
          rafRef.current = requestAnimationFrame(detectLoop);
          return;
        }
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
        ctx.drawImage(video, 0, 0, w, h);
        const imageData = ctx.getImageData(0, 0, w, h);

        const results = await detector.detect(imageData);
        if (!mountedRef.current) return;

        if (detectCount % 20 === 0) {
          setDebugInfo(`Scanning... (${detectCount} frames, ${w}x${h})`);
        }

        if (results.length > 0) {
          const result = results[0];
          const code = result.rawValue;
          const format = (result as any).format || '';
          const foodFormats = ['ean_13', 'ean_8', 'upc_a', 'upc_e'];
          const isDigitCode = /^\d{8,14}$/.test(code);
          const isFoodBarcode = foodFormats.includes(format) || isDigitCode;
          // QR codes with non-digit payloads (URLs, etc.) get routed through
          // onQrScan when provided - the server classifies the payload.
          const isQrPayload = format === 'qr_code' && !isDigitCode && !!onQrScanRef.current;
          const ts = Date.now();
          const last = lastScanRef.current;

          if (code === last.code && ts - last.time <= 3000) {
            hapticLight();
            setFlash('yellow');
            setDupText(true);
            if (dupTimeoutRef.current) clearTimeout(dupTimeoutRef.current);
            dupTimeoutRef.current = setTimeout(() => {
              if (mountedRef.current) { setFlash(false); setDupText(false); }
            }, 600);
            rafRef.current = requestAnimationFrame(detectLoop);
            return;
          }

          // QR payload (non-digit) with a handler wired up: route to the QR
          // endpoint, which will fetch and classify server-side.
          if (isQrPayload) {
            lastScanRef.current = { code, time: ts };
            if (scanTimeoutRef.current) { clearTimeout(scanTimeoutRef.current); scanTimeoutRef.current = null; }
            if (scanSlowRef.current) { clearTimeout(scanSlowRef.current); scanSlowRef.current = null; }
            if (scanWarningRef.current) { clearTimeout(scanWarningRef.current); scanWarningRef.current = null; }
            hapticSuccess();
            trackEvent('qr_scanned');
            setFlash('green');
            if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
            flashTimeoutRef.current = setTimeout(() => {
              if (mountedRef.current) setFlash(false);
            }, 400);
            setDebugInfo(`QR: ${code.slice(0, 48)}${code.length > 48 ? '…' : ''}`);
            stopCamera();
            try {
              onQrScanRef.current?.(code);
            } catch (qrErr) {
              console.error('onQrScan callback error:', qrErr);
            }
            return;
          }

          // Non-food barcode detected - stop camera and show options
          if (!isFoodBarcode) {
            lastScanRef.current = { code, time: ts };
            if (scanTimeoutRef.current) { clearTimeout(scanTimeoutRef.current); scanTimeoutRef.current = null; }
            if (scanSlowRef.current) { clearTimeout(scanSlowRef.current); scanSlowRef.current = null; }
            if (scanWarningRef.current) { clearTimeout(scanWarningRef.current); scanWarningRef.current = null; }
            hapticLight();
            stopCamera();
            setNonFoodBarcode({ code, format: format || 'unknown' });
            return;
          }

          lastScanRef.current = { code, time: ts };
          if (scanTimeoutRef.current) { clearTimeout(scanTimeoutRef.current); scanTimeoutRef.current = null; }
          if (scanSlowRef.current) { clearTimeout(scanSlowRef.current); scanSlowRef.current = null; }
          if (scanWarningRef.current) { clearTimeout(scanWarningRef.current); scanWarningRef.current = null; }
          hapticSuccess();
          trackEvent('barcode_scanned');
          setFlash('green');
          if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
          flashTimeoutRef.current = setTimeout(() => {
            if (mountedRef.current) setFlash(false);
          }, 400);
          setDebugInfo(`Found: ${code}`);
          consecutiveFailsRef.current = 0;
          // Stop the camera feed - no need to keep scanning
          stopCamera();
          try {
            onScanRef.current(code);
          } catch (scanErr) {
            console.error('onScan callback error:', scanErr);
          }
          return;
        }
        consecutiveFailsRef.current = 0;
      } catch (err) {
        consecutiveFailsRef.current++;
        if (detectCount <= 3) {
          setDebugInfo(`Error: ${err instanceof Error ? err.message : String(err)}`);
        }
        if (consecutiveFailsRef.current > 50) {
          if (Sentry.isInitialized()) Sentry.captureMessage('Barcode detection failed repeatedly', { level: 'warning', tags: { component: 'BarcodeScanner' } });
          setCameraError('Barcode detection failed repeatedly. Please close and try again.');
          return;
        }
      }

      rafRef.current = requestAnimationFrame(detectLoop);
    };

    const acquireCamera = async (): Promise<MediaStream> => {
      const constraints = {
        video: {
          facingMode: { ideal: 'environment' as const },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
      };
      try {
        return await navigator.mediaDevices.getUserMedia(constraints);
      } catch (err: unknown) {
        // Camera hardware may still be releasing from a previous session -
        // retry once after a short delay before giving up
        const name = err instanceof DOMException ? err.name : '';
        if (name === 'NotReadableError' || name === 'AbortError') {
          await new Promise(resolve => setTimeout(resolve, 300));
          if (!mountedRef.current) throw err;
          return await navigator.mediaDevices.getUserMedia(constraints);
        }
        throw err;
      }
    };

    const startCamera = async () => {
      // Check for camera API availability
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.error('Camera API not available. Secure context:', window.isSecureContext, 'Protocol:', location.protocol);
        if (mountedRef.current) {
          setCameraErrorType('no_api');
          setCameraError(
            !window.isSecureContext
              ? 'Camera requires HTTPS. Please open the app via a secure URL.'
              : 'Camera not available on this device/browser.'
          );
        }
        return;
      }

      try {
        setDebugInfo('Requesting camera access...');
        const stream = await acquireCamera();
        if (!mountedRef.current) { stream.getTracks().forEach(t => t.stop()); return; }
        streamRef.current = stream;

        const track = stream.getVideoTracks()[0];
        if (track) {
          const caps = (track as any).getCapabilities?.();
          if (caps?.torch) setTorchAvailable(true);
          const settings = track.getSettings();
          setDebugInfo(`Camera: ${settings.width}x${settings.height}`);
        }

        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          video.oncanplay = () => {
            if (!rafRef.current && mountedRef.current) {
              setDebugInfo(`Detecting at ${video.videoWidth}x${video.videoHeight}...`);
              rafRef.current = requestAnimationFrame(detectLoop);
              // Start scan timeout - progressive feedback then timeout
              if (!scanTimeoutRef.current) {
                setScanPhase('scanning');
                scanSlowRef.current = setTimeout(() => {
                  if (mountedRef.current) setScanPhase('slow');
                }, 5000);
                scanWarningRef.current = setTimeout(() => {
                  if (mountedRef.current) setScanPhase('warning');
                }, 8000);
                scanTimeoutRef.current = setTimeout(() => {
                  if (mountedRef.current) setTimedOut(true);
                }, 10000);
              }
            }
          };
          try {
            await video.play();
          } catch (playErr) {
            console.error('video.play() rejected:', playErr);
            if (mountedRef.current) {
              setCameraError('Could not start camera video. Please close and try again.');
            }
            return;
          }
          if (video.readyState >= 3 && !rafRef.current) {
            rafRef.current = requestAnimationFrame(detectLoop);
          }
        }
      } catch (err: unknown) {
        console.error('Camera error:', err);
        if (mountedRef.current) {
          const name = err instanceof DOMException ? err.name : '';
          if (name === 'NotAllowedError') {
            setCameraErrorType('denied');
            setCameraError('Camera access blocked');
          } else if (name === 'NotFoundError') {
            setCameraErrorType('not_found');
            setCameraError('No camera found on this device');
          } else if (name === 'NotReadableError' || name === 'AbortError') {
            setCameraErrorType('in_use');
            setCameraError('Camera is in use by another app');
          } else {
            setCameraErrorType('other');
            setCameraError(`Camera error: ${err instanceof Error ? err.message : String(err)}`);
            if (Sentry.isInitialized()) Sentry.captureException(err, { tags: { component: 'BarcodeScanner', stage: 'camera-access' } });
          }
        }
      }
    };

    startCamera();

    return () => {
      mountedRef.current = false;
      // BarcodeDetectorPolyfill doesn't have a destroy() method,
      // so we release the reference to allow garbage collection.
      detector = null;
      stopCamera();
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current);
      if (dupTimeoutRef.current) clearTimeout(dupTimeoutRef.current);
      if (scanTimeoutRef.current) clearTimeout(scanTimeoutRef.current);
      if (scanSlowRef.current) clearTimeout(scanSlowRef.current);
      if (scanWarningRef.current) clearTimeout(scanWarningRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- cameraKey triggers restart (e.g. after non-food barcode rescan)
  }, [stopCamera, cameraKey]);

  const cornerColor = flash === 'green' ? '#10b981' : flash === 'yellow' ? '#eab308' : scanPhase === 'warning' ? '#f59e0b' : 'rgba(255,255,255,0.6)';

  return (
    <div className="fixed inset-0 z-[100]" style={{ background: '#000' }}>
      <style>{`
        @keyframes barcode-sweep {
          0% { top: 0%; opacity: 0; }
          10% { opacity: 1; }
          90% { opacity: 1; }
          100% { top: 100%; opacity: 0; }
        }
      `}</style>

      <video ref={videoRef} playsInline muted autoPlay className="absolute inset-0 w-full h-full object-cover" />

      <div className="absolute inset-0 flex items-center justify-center">
        <div className="relative transition-all duration-300"
          style={{ width: '75%', maxWidth: '400px', aspectRatio: '3 / 1', borderRadius: '12px', boxShadow: '0 0 0 9999px rgba(0,0,0,0.7)' }}>
          {[['top', 'left', 'TopLeft'], ['top', 'right', 'TopRight'], ['bottom', 'left', 'BottomLeft'], ['bottom', 'right', 'BottomRight']].map(([v, h, r]) => (
            <div key={r} className="absolute transition-colors duration-300" style={{
              [v]: -2, [h]: -2, width: 24, height: 24,
              [`border${r.includes('Top') ? 'Top' : 'Bottom'}`]: `3px solid ${cornerColor}`,
              [`border${r.includes('Left') ? 'Left' : 'Right'}`]: `3px solid ${cornerColor}`,
              [`border${r}Radius`]: 12,
            }} />
          ))}
          <div className="absolute left-2 right-2 h-0.5 rounded-full pointer-events-none"
            style={{ background: 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.7) 50%, transparent 100%)', boxShadow: '0 0 8px rgba(255,255,255,0.3)', animation: 'barcode-sweep 2s ease-in-out infinite' }} />
        </div>
      </div>

      <div className="absolute left-0 right-0 flex justify-center" style={{ top: 'calc(50% - 15vw - 48px)' }}>
        <span className="text-sm font-medium px-4 py-2 rounded-full" style={{
          background: dupText ? 'rgba(234,179,8,0.3)' : 'rgba(0,0,0,0.6)',
          color: dupText ? '#fde047' : 'rgba(255,255,255,0.9)',
          backdropFilter: 'blur(8px)',
        }}>
          {dupText ? 'Already scanned' : 'Point at barcode'}
        </span>
      </div>

      {/* Intermediate scan feedback - appears at 5s */}
      {(scanPhase === 'slow' || scanPhase === 'warning') && !timedOut && !cameraError && (
        <div className="absolute left-0 right-0 flex justify-center" style={{ top: 'calc(50% + 15vw + 16px)' }}>
          <span className="text-xs font-medium px-3 py-1.5 rounded-full animate-pulse" style={{
            background: scanPhase === 'warning' ? 'rgba(245,158,11,0.25)' : 'rgba(0,0,0,0.6)',
            color: scanPhase === 'warning' ? '#fbbf24' : 'rgba(255,255,255,0.7)',
            backdropFilter: 'blur(8px)',
          }}>
            {scanPhase === 'warning'
              ? 'Make sure the barcode is well-lit and in frame'
              : 'Try moving closer or tilting the barcode'}
          </span>
        </div>
      )}

      <div className="absolute bottom-8 left-0 right-0 flex justify-center">
        <span className="text-[11px] px-3 py-1 rounded-full" style={{ background: 'rgba(0,0,0,0.6)', color: 'rgba(255,255,255,0.5)' }}>
          {debugInfo}
        </span>
      </div>

      {torchAvailable && (
        <button onClick={toggleTorch} className="absolute top-4 left-4 w-10 h-10 rounded-full flex items-center justify-center"
          style={{ background: torchOn ? 'rgba(250,204,21,0.3)' : 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)', marginTop: 'env(safe-area-inset-top, 0px)', border: torchOn ? '1px solid rgba(250,204,21,0.5)' : '1px solid rgba(255,255,255,0.15)' }}
          aria-label={torchOn ? 'Turn off flashlight' : 'Turn on flashlight'}>
          <Zap className="w-5 h-5" style={{ color: torchOn ? '#facc15' : '#fff' }} />
        </button>
      )}

      {timedOut && !cameraError && (
        <div className="absolute inset-0 flex flex-col items-center justify-center px-6" style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(12px)' }}>
          <div className="w-full max-w-sm space-y-4">
            {/* Message card */}
            <div className="rounded-2xl p-5 text-center space-y-2" style={{ background: 'rgba(0,0,0,0.6)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <p className="text-3xl">🤷</p>
              <p className="text-white text-sm font-semibold">No barcode detected</p>
              <p className="text-xs" style={{ color: 'rgba(255,255,255,0.45)' }}>
                Make sure you're scanning a food product barcode (EAN/UPC). Log it another way:
              </p>
            </div>
            {/* FAB-style action cards */}
            <div className="grid grid-cols-2 gap-2.5">
              {([
                onSwitchPhoto && { emoji: '📸', label: 'Photo', sub: 'Snap the label', color: '#10b981', action: onSwitchPhoto },
                onSwitchText && { emoji: '✏️', label: 'Text', sub: 'Type what it is', color: '#3b82f6', action: onSwitchText },
                { emoji: '🏷️', label: 'Try Again', sub: 'Scan another code', color: '#06b6d4', action: () => { setTimedOut(false); setScanPhase('scanning'); if (scanSlowRef.current) clearTimeout(scanSlowRef.current); if (scanWarningRef.current) clearTimeout(scanWarningRef.current); if (scanTimeoutRef.current) clearTimeout(scanTimeoutRef.current); scanSlowRef.current = setTimeout(() => { if (mountedRef.current) setScanPhase('slow'); }, 5000); scanWarningRef.current = setTimeout(() => { if (mountedRef.current) setScanPhase('warning'); }, 8000); scanTimeoutRef.current = setTimeout(() => { if (mountedRef.current) setTimedOut(true); }, 10000); } },
                onManualEntry && { emoji: '🔢', label: 'Type barcode', sub: 'Enter code manually', color: '#a855f7', action: onManualEntry },
              ].filter(Boolean) as { emoji: string; label: string; sub: string; color: string; action: () => void }[]).map((opt, i) => (
                <button
                  key={opt.label}
                  onClick={() => { hapticLight(); opt.action(); }}
                  className="relative flex flex-col items-center gap-2 px-3 py-4 rounded-[20px] text-center active:scale-[0.96] overflow-hidden"
                  style={{
                    background: `linear-gradient(145deg, ${opt.color}0C 0%, rgba(15,23,42,0.8) 50%, ${opt.color}06 100%)`,
                    border: `1px solid ${opt.color}20`,
                    boxShadow: `0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.05), 0 0 40px ${opt.color}10`,
                    backdropFilter: 'blur(32px)',
                    animation: `fabIn 280ms ${30 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1) both`,
                  }}
                >
                  <div className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-3xl pointer-events-none" style={{ background: `${opt.color}18` }} />
                  <div
                    className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl shrink-0"
                    style={{
                      background: `linear-gradient(145deg, ${opt.color}30 0%, ${opt.color}12 100%)`,
                      border: `1px solid ${opt.color}35`,
                      boxShadow: `0 6px 20px ${opt.color}20, inset 0 1px 0 ${opt.color}20`,
                    }}
                  >
                    {opt.emoji}
                  </div>
                  <span className="text-xs font-semibold text-white">{opt.label}</span>
                  <span className="text-[10px] leading-tight mt-0.5" style={{ color: 'rgba(255,255,255,0.5)' }}>{opt.sub}</span>
                </button>
              ))}
            </div>
            <button onClick={onClose} className="w-full text-center py-2 text-sm font-semibold" style={{ color: 'rgba(255,255,255,0.5)' }}>
              Go Back
            </button>
          </div>
        </div>
      )}

      {cameraError && (
        <div className="absolute inset-0 flex flex-col items-center justify-center px-6 overflow-y-auto py-6" style={{ background: 'rgba(0,0,0,0.82)', backdropFilter: 'blur(16px)' }}>
          <div className="w-full max-w-sm space-y-4">
            {/* Message card */}
            <div className="rounded-2xl p-5 text-center space-y-2" style={{ background: 'rgba(0,0,0,0.6)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <p className="text-3xl">
                {cameraErrorType === 'denied' ? '🔒' : cameraErrorType === 'not_found' ? '📷' : cameraErrorType === 'in_use' ? '⏳' : '⚠️'}
              </p>
              <p className="text-white text-sm font-semibold">{cameraError}</p>
              <p className="text-xs" style={{ color: 'rgba(255,255,255,0.5)' }}>
                {cameraErrorType === 'denied'
                  ? "No worries - you can still scan by taking a photo of the barcode, or type it in."
                  : cameraErrorType === 'in_use'
                    ? 'Close other apps using the camera, or snap a photo of the barcode instead.'
                    : cameraErrorType === 'not_found'
                      ? 'You can still scan by taking a photo of the barcode, or type it in.'
                      : "Try one of the options below to keep logging."}
              </p>
            </div>

            {/* File-picker detection error (shown after a failed photo attempt) */}
            {fileError && (
              <div className="rounded-2xl p-4 text-center" style={{ background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)' }}>
                <p className="text-xs" style={{ color: '#fbbf24' }}>{fileError}</p>
              </div>
            )}

            {/* FAB-style action cards */}
            <div className="grid grid-cols-2 gap-2.5">
              {([
                { emoji: '📸', label: 'Upload Photo', sub: 'Snap the barcode', color: '#10b981', action: openFilePicker },
                onManualEntry && { emoji: '🔢', label: 'Type barcode', sub: 'Enter code manually', color: '#a855f7', action: onManualEntry },
                onSwitchPhoto && { emoji: '🍽️', label: 'Photo', sub: 'Scan the food', color: '#06b6d4', action: onSwitchPhoto },
                onSwitchText && { emoji: '✏️', label: 'Text', sub: 'Type the meal', color: '#3b82f6', action: onSwitchText },
              ].filter(Boolean) as { emoji: string; label: string; sub: string; color: string; action: () => void }[]).map((opt, i) => (
                <button
                  key={opt.label}
                  onClick={() => { hapticLight(); opt.action(); }}
                  disabled={fileDetecting}
                  className="relative flex flex-col items-center gap-2 px-3 py-4 rounded-[20px] text-center active:scale-[0.96] overflow-hidden disabled:opacity-50"
                  style={{
                    background: `linear-gradient(145deg, ${opt.color}0C 0%, rgba(15,23,42,0.8) 50%, ${opt.color}06 100%)`,
                    border: `1px solid ${opt.color}20`,
                    boxShadow: `0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.05), 0 0 40px ${opt.color}10`,
                    backdropFilter: 'blur(32px)',
                    animation: `fabIn 280ms ${30 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1) both`,
                  }}
                >
                  <div className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-3xl pointer-events-none" style={{ background: `${opt.color}18` }} />
                  <div
                    className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl shrink-0"
                    style={{
                      background: `linear-gradient(145deg, ${opt.color}30 0%, ${opt.color}12 100%)`,
                      border: `1px solid ${opt.color}35`,
                      boxShadow: `0 6px 20px ${opt.color}20, inset 0 1px 0 ${opt.color}20`,
                    }}
                  >
                    {opt.emoji}
                  </div>
                  <span className="text-xs font-semibold text-white">{opt.label}</span>
                  <span className="text-[10px] leading-tight mt-0.5" style={{ color: 'rgba(255,255,255,0.5)' }}>{opt.sub}</span>
                </button>
              ))}
            </div>

            {/* Try Again (retries getUserMedia - works once permission is reset) */}
            <button
              onClick={handleTryAgain}
              disabled={fileDetecting}
              className="w-full py-2.5 rounded-xl text-xs font-semibold text-white active:scale-[0.98] disabled:opacity-50"
              style={{ background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.15)' }}
            >
              Try camera again
            </button>

            {/* Collapsible platform-specific permission instructions */}
            {cameraErrorType === 'denied' && (
              <div className="rounded-2xl overflow-hidden" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <button
                  onClick={() => setShowInstructions(v => !v)}
                  className="w-full px-4 py-3 flex items-center justify-between text-xs font-semibold text-white"
                >
                  <span>How to re-enable the camera</span>
                  <span style={{ color: 'rgba(255,255,255,0.5)' }}>{showInstructions ? '▲' : '▼'}</span>
                </button>
                {showInstructions && (() => {
                  const info = getPermissionInstructions();
                  return (
                    <div className="px-4 pb-4 space-y-2">
                      <p className="text-[10px] uppercase tracking-wider" style={{ color: 'rgba(255,255,255,0.4)' }}>
                        {info.platform}
                      </p>
                      <ol className="space-y-1.5">
                        {info.steps.map((step, i) => (
                          <li key={i} className="text-xs flex gap-2" style={{ color: 'rgba(255,255,255,0.75)' }}>
                            <span className="shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold" style={{ background: 'rgba(255,255,255,0.1)' }}>
                              {i + 1}
                            </span>
                            <span>{step}</span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  );
                })()}
              </div>
            )}

            <button onClick={onClose} className="w-full text-center py-2 text-sm font-semibold" style={{ color: 'rgba(255,255,255,0.5)' }}>
              Go Back
            </button>
          </div>

          {/* Full-screen blocking spinner while multi-pass detection runs */}
          {fileDetecting && (
            <div className="celebration-overlay fixed inset-0 flex items-center justify-center pointer-events-none" style={{ background: 'rgba(0,0,0,0.5)' }}>
              <div className="rounded-2xl px-5 py-4 flex items-center gap-3" style={{ background: 'rgba(15,23,42,0.9)', border: '1px solid rgba(255,255,255,0.1)' }}>
                <LoadingSpinner size="sm" />
                <span className="text-xs text-white font-medium">Reading barcode…</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Hidden file input - opens the native camera via capture="environment" */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: 'none' }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFileCapture(file);
          e.target.value = ''; // allow re-picking the same file
        }}
      />

      {/* Non-food barcode detected - FAB-style options */}
      {nonFoodBarcode && (
        <div className="absolute inset-0 flex flex-col items-center justify-center px-6" style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(12px)' }}>
          <div className="w-full max-w-sm space-y-4">
            {/* Message card */}
            <div className="rounded-2xl p-5 text-center space-y-2" style={{ background: 'rgba(0,0,0,0.6)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <p className="text-3xl">🏷️</p>
              <p className="text-white text-sm font-semibold">Barcode detected</p>
              <p className="text-xs font-mono tracking-wider" style={{ color: 'rgba(245,158,11,0.8)' }}>
                {nonFoodBarcode.code}
              </p>
              <p className="text-xs" style={{ color: 'rgba(255,255,255,0.45)' }}>
                This is a {nonFoodBarcode.format.replace('_', ' ')} code, not a food product barcode. Log it another way:
              </p>
            </div>
            {/* FAB-style action cards */}
            <div className="grid grid-cols-2 gap-2.5">
              {([
                onSwitchPhoto && { emoji: '📸', label: 'Photo', sub: 'Snap the label', color: '#10b981', action: onSwitchPhoto },
                onSwitchText && { emoji: '✏️', label: 'Text', sub: 'Type what it is', color: '#3b82f6', action: onSwitchText },
                { emoji: '🏷️', label: 'Rescan', sub: 'Try another code', color: '#06b6d4', action: () => { setNonFoodBarcode(null); setCameraKey(k => k + 1); } },
                onManualEntry && { emoji: '🔢', label: 'Type barcode', sub: 'Enter code manually', color: '#a855f7', action: onManualEntry },
              ].filter(Boolean) as { emoji: string; label: string; sub: string; color: string; action: () => void }[]).map((opt, i) => (
                <button
                  key={opt.label}
                  onClick={() => { hapticLight(); opt.action(); }}
                  className="relative flex flex-col items-center gap-2 px-3 py-4 rounded-[20px] text-center active:scale-[0.96] overflow-hidden"
                  style={{
                    background: `linear-gradient(145deg, ${opt.color}0C 0%, rgba(15,23,42,0.8) 50%, ${opt.color}06 100%)`,
                    border: `1px solid ${opt.color}20`,
                    boxShadow: `0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.05), 0 0 40px ${opt.color}10`,
                    backdropFilter: 'blur(32px)',
                    animation: `fabIn 280ms ${30 + i * 35}ms cubic-bezier(0.32, 0.72, 0, 1) both`,
                  }}
                >
                  <div className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-3xl pointer-events-none" style={{ background: `${opt.color}18` }} />
                  <div
                    className="w-12 h-12 rounded-2xl flex items-center justify-center text-2xl shrink-0"
                    style={{
                      background: `linear-gradient(145deg, ${opt.color}30 0%, ${opt.color}12 100%)`,
                      border: `1px solid ${opt.color}35`,
                      boxShadow: `0 6px 20px ${opt.color}20, inset 0 1px 0 ${opt.color}20`,
                    }}
                  >
                    {opt.emoji}
                  </div>
                  <span className="text-xs font-semibold text-white">{opt.label}</span>
                  <span className="text-[10px] leading-tight mt-0.5" style={{ color: 'rgba(255,255,255,0.5)' }}>{opt.sub}</span>
                </button>
              ))}
            </div>
            <button onClick={onClose} className="w-full text-center py-2 text-sm font-semibold" style={{ color: 'rgba(255,255,255,0.5)' }}>
              Go Back
            </button>
          </div>
        </div>
      )}

      <button onClick={onClose} aria-label="Close barcode scanner"
        className="absolute top-4 right-4 w-10 h-10 rounded-full flex items-center justify-center"
        style={{ background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)', marginTop: 'env(safe-area-inset-top, 0px)' }}>
        <X className="w-5 h-5 text-white" />
      </button>
    </div>
  );
}
