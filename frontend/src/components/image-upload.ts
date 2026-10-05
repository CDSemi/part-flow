// Shared image upload preparation (Worker avatars; later Part Number and
// User images). The server accepts PNG, JPEG or WebP images of at most
// 2 MiB whose declared type matches their content. This helper makes a
// chosen file fit that contract BEFORE any write is sent:
//
//  1. the content type is sniffed from the file's own magic bytes (the
//     browser's `File.type` only reflects the file name extension);
//  2. the image is decoded (orientation applied) to read its size;
//  3. a small enough image is passed through with its bytes unchanged,
//     labelled with the sniffed type;
//  4. a larger one is downscaled to the maximum edge and re-encoded.
//
// The server stays authoritative and re-checks type and size.
//
// Production-safe: no mock data, no framework imports.

/** Largest accepted upload (bytes), the same limit as the server. */
const MAX_IMAGE_BYTES = 2 * 1024 * 1024;

const ALLOWED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

const UNSUPPORTED_MESSAGE = 'Choose a PNG, JPEG or WebP image.';
const TOO_LARGE_MESSAGE =
  'This image is too large even after resizing. Choose a smaller image.';

/** A chosen file that cannot be uploaded; `message` is user-facing. */
export class ImageUploadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ImageUploadError';
  }
}

/** Read the first `count` bytes of a blob. */
function readHead(blob: Blob, count: number): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob.slice(0, count));
  });
}

function startsWith(bytes: Uint8Array, signature: number[], offset = 0) {
  return signature.every((value, index) => bytes[offset + index] === value);
}

/** The image type the bytes carry (PNG, JPEG or WebP), or null. */
function sniffImageType(bytes: Uint8Array): string | null {
  if (startsWith(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) {
    return 'image/png';
  }
  if (startsWith(bytes, [0xff, 0xd8, 0xff])) return 'image/jpeg';
  if (
    startsWith(bytes, [0x52, 0x49, 0x46, 0x46]) && // RIFF
    startsWith(bytes, [0x57, 0x45, 0x42, 0x50], 8) // WEBP
  ) {
    return 'image/webp';
  }
  return null;
}

function encodeCanvas(
  canvas: HTMLCanvasElement,
  type: string,
): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, 0.9));
}

/**
 * Prepare one chosen image file for upload: a Blob whose type always
 * matches its bytes, at most `maxEdge` pixels on its longest edge
 * unless it was passed through unchanged, and at most 2 MiB. Rejects
 * with an `ImageUploadError` carrying the user-facing reason.
 */
export async function prepareImageUpload(
  file: File,
  maxEdge = 1024,
): Promise<Blob> {
  let sniffedType: string | null;
  try {
    sniffedType = sniffImageType(await readHead(file, 12));
  } catch {
    throw new ImageUploadError(UNSUPPORTED_MESSAGE);
  }
  if (sniffedType === null) throw new ImageUploadError(UNSUPPORTED_MESSAGE);

  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch {
    throw new ImageUploadError(UNSUPPORTED_MESSAGE);
  }

  try {
    const longestEdge = Math.max(bitmap.width, bitmap.height);
    if (longestEdge <= maxEdge && file.size <= MAX_IMAGE_BYTES) {
      // Small enough: the original bytes, labelled with their real type.
      return file.type === sniffedType
        ? file
        : new Blob([file], { type: sniffedType });
    }

    // Downscale (never upscale) and re-encode in the sniffed type.
    const scale = Math.min(1, maxEdge / longestEdge);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new ImageUploadError(UNSUPPORTED_MESSAGE);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);

    const encoded = await encodeCanvas(canvas, sniffedType);
    // A browser that cannot encode the requested type answers with
    // another one (typically PNG); the canvas output stays consistent
    // with its own type, so any allowed type is accepted.
    if (!encoded || !ALLOWED_IMAGE_TYPES.includes(encoded.type)) {
      throw new ImageUploadError(UNSUPPORTED_MESSAGE);
    }
    if (encoded.size > MAX_IMAGE_BYTES) {
      throw new ImageUploadError(TOO_LARGE_MESSAGE);
    }
    return encoded;
  } finally {
    bitmap.close();
  }
}
