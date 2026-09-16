import { useEffect, useState } from "react";
import {
  previewCoverHttpSrc,
  previewCoverSrc,
  previewVideoHttpSrc,
  previewVideoSrc,
} from "./mediaPreview";

type MediaVideoProps = {
  outputId: number;
  localPath?: string | null;
  bust?: string | number | null;
  /** When false, caller should not mount this (missing file). */
  localOk?: boolean;
  className?: string;
  onFatalError?: () => void;
  draggable?: boolean;
  onDragStart?: (e: React.DragEvent<HTMLVideoElement>) => void;
};

/**
 * <video> with asset:// → engine HTTP fallback so stale convertFileSrc
 * does not leave a permanently broken player.
 */
export function MediaVideo({
  outputId,
  localPath,
  bust,
  localOk = true,
  className,
  onFatalError,
  draggable,
  onDragStart,
}: MediaVideoProps) {
  const localSrc = previewVideoSrc(outputId, localPath, bust, { localOk });
  const httpSrc = previewVideoHttpSrc(outputId, bust);
  const [src, setSrc] = useState(localSrc);
  const [usedHttp, setUsedHttp] = useState(localSrc === httpSrc);

  useEffect(() => {
    setSrc(localSrc);
    setUsedHttp(localSrc === httpSrc);
  }, [localSrc, httpSrc, outputId, bust]);

  return (
    <video
      key={`mv-${outputId}-${bust ?? 0}-${src}`}
      className={className}
      controls
      preload="metadata"
      playsInline
      src={src}
      draggable={draggable}
      onDragStart={onDragStart}
      onError={() => {
        if (!usedHttp && src !== httpSrc) {
          setUsedHttp(true);
          setSrc(httpSrc);
          return;
        }
        onFatalError?.();
      }}
    />
  );
}

type CoverThumbProps = {
  outputId: number;
  index: number;
  localPath?: string | null;
  bust?: string | number | null;
  localOk?: boolean;
  className?: string;
  alt?: string;
  fallbackLabel?: string;
};

/** Cover <img> with HTTP fallback then text fallback (never leave WebKit "?"). */
export function CoverThumb({
  outputId,
  index,
  localPath,
  bust,
  localOk = true,
  className,
  alt,
  fallbackLabel = "帧不可用",
}: CoverThumbProps) {
  const path = (localPath || "").trim();
  const primary = path
    ? previewCoverSrc(outputId, index, path, bust, {
        localOk,
        preferHttp: !localOk,
      })
    : "";
  const httpSrc = previewCoverHttpSrc(outputId, index, bust);
  const [src, setSrc] = useState(primary);
  const [failed, setFailed] = useState(!path);
  const [usedHttp, setUsedHttp] = useState(!path || primary === httpSrc || !localOk);

  useEffect(() => {
    if (!path) {
      setFailed(true);
      setSrc("");
      return;
    }
    setSrc(primary);
    setFailed(false);
    setUsedHttp(primary === httpSrc || !localOk);
  }, [primary, httpSrc, path, localOk, outputId, index, bust]);

  if (failed || !src) {
    return (
      <span className={`cover-thumb-fallback${className ? ` ${className}` : ""}`} role="img" aria-label={fallbackLabel}>
        {fallbackLabel}
      </span>
    );
  }

  return (
    <img
      src={src}
      alt={alt || `封面 ${index + 1}`}
      className={className}
      draggable={false}
      onError={() => {
        if (!usedHttp && src !== httpSrc) {
          setUsedHttp(true);
          setSrc(httpSrc);
          return;
        }
        setFailed(true);
      }}
    />
  );
}
