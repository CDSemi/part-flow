import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ImageUploadError, prepareImageUpload } from './image-upload';

// Image upload preparation: the type comes from the bytes (never the
// file name), a small image passes through unchanged, a large one is
// downscaled and re-encoded in its own type, and nothing above the
// server limit is ever offered for upload. jsdom decodes and draws no
// images, so createImageBitmap and the canvas are stubbed.

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d];
const JPEG = [0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46, 0x49, 0x46, 0, 1];
const WEBP = [0x52, 0x49, 0x46, 0x46, 0x24, 0, 0, 0, 0x57, 0x45, 0x42, 0x50];
const GIF = [0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 1, 0, 1, 0, 0, 0];
const MAX = 2 * 1024 * 1024;

function imageFile(
  signature: number[],
  type: string,
  size = 1000,
  name = 'photo',
): File {
  const bytes = new Uint8Array(Math.max(size, signature.length));
  bytes.set(signature);
  return new File([bytes], name, { type });
}

let bitmap: { width: number; height: number; close: () => void };
let encodedSize: number;
let encodedType: string | null;
const drawImage = vi.fn();

function stubToBlob() {
  return vi
    .spyOn(HTMLCanvasElement.prototype, 'toBlob')
    .mockImplementation((callback: BlobCallback, type?: string) => {
      callback(
        new Blob([new Uint8Array(encodedSize)], {
          type: encodedType ?? type ?? 'image/png',
        }),
      );
    });
}

let toBlob: ReturnType<typeof stubToBlob>;

beforeEach(() => {
  bitmap = { width: 800, height: 600, close: vi.fn() };
  encodedSize = 300_000;
  encodedType = null;
  drawImage.mockClear();
  vi.stubGlobal(
    'createImageBitmap',
    vi.fn(async () => bitmap),
  );
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () => ({ drawImage }) as unknown as CanvasRenderingContext2D,
  );
  toBlob = stubToBlob();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test('an unknown signature is rejected whatever the file claims to be', async () => {
  for (const file of [
    imageFile(GIF, 'image/png', 1000, 'animated.png'),
    new File(['just some text, not an image'], 'notes.jpg', {
      type: 'image/jpeg',
    }),
  ]) {
    await expect(prepareImageUpload(file)).rejects.toEqual(
      new ImageUploadError('Choose a PNG, JPEG or WebP image.'),
    );
  }
  expect(createImageBitmap).not.toHaveBeenCalled();
});

test('an undecodable image is rejected with the same message', async () => {
  vi.stubGlobal(
    'createImageBitmap',
    vi.fn(async () => {
      throw new DOMException('broken', 'InvalidStateError');
    }),
  );
  await expect(
    prepareImageUpload(imageFile(PNG, 'image/png')),
  ).rejects.toBeInstanceOf(ImageUploadError);
});

test('a small, correctly labelled image is returned unchanged', async () => {
  for (const [signature, type] of [
    [PNG, 'image/png'],
    [JPEG, 'image/jpeg'],
    [WEBP, 'image/webp'],
  ] as const) {
    const file = imageFile([...signature], type);
    expect(await prepareImageUpload(file)).toBe(file);
  }
  expect(toBlob).not.toHaveBeenCalled();
  expect(bitmap.close).toHaveBeenCalled();
});

test('a mislabelled small image is relabelled with its sniffed type, same bytes', async () => {
  const file = imageFile(PNG, 'image/jpeg', 5000, 'photo.jpg');
  const prepared = await prepareImageUpload(file);
  expect(prepared).not.toBe(file);
  expect(prepared.type).toBe('image/png');
  expect(prepared.size).toBe(file.size);
  expect(toBlob).not.toHaveBeenCalled();
});

test('a large image is drawn at the maximum edge and encoded in its own type', async () => {
  bitmap = { width: 3000, height: 2000, close: vi.fn() };
  const file = imageFile(JPEG, 'image/jpeg', 4_000_000);
  const prepared = await prepareImageUpload(file);

  expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 1024, 683);
  const canvas = toBlob.mock.contexts[0] as HTMLCanvasElement;
  expect([canvas.width, canvas.height]).toEqual([1024, 683]);
  expect(toBlob.mock.calls[0][1]).toBe('image/jpeg');
  expect(toBlob.mock.calls[0][2]).toBe(0.9);
  expect(prepared.type).toBe('image/jpeg');
  expect(prepared.size).toBe(300_000);
  expect(bitmap.close).toHaveBeenCalled();
});

test('a byte-heavy image within the edge limit is re-encoded, never upscaled', async () => {
  bitmap = { width: 900, height: 700, close: vi.fn() };
  const prepared = await prepareImageUpload(
    imageFile(PNG, 'image/png', MAX + 1),
  );
  expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 900, 700);
  expect(prepared.type).toBe('image/png');
});

test('a browser falling back to another allowed type is accepted', async () => {
  bitmap = { width: 2048, height: 2048, close: vi.fn() };
  encodedType = 'image/png';
  const prepared = await prepareImageUpload(
    imageFile(WEBP, 'image/webp', 3_000_000),
  );
  expect(toBlob.mock.calls[0][1]).toBe('image/webp');
  expect(prepared.type).toBe('image/png');
});

test('a result still above the limit after resizing is rejected', async () => {
  bitmap = { width: 4000, height: 3000, close: vi.fn() };
  encodedSize = MAX + 1;
  await expect(
    prepareImageUpload(imageFile(PNG, 'image/png', 9_000_000)),
  ).rejects.toEqual(
    new ImageUploadError(
      'This image is too large even after resizing. Choose a smaller image.',
    ),
  );
  expect(bitmap.close).toHaveBeenCalled();
});
