// Every photo is normalised before upload (docs/AR_PHOTO.md): EXIF rotation applied by the decoder, long side at most
// 4096 px, JPEG at quality 0.92. The stored photo then has no EXIF rotation and its pixel grid is the one all numbers use.
const MAX_SIDE = 4096;

export async function normalizePhoto(file: File): Promise<File> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new Error(`${file.name}: this file cannot be read as a photo`);
  }
  try {
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("This browser cannot prepare the photo");
    ctx.drawImage(bitmap, 0, 0, width, height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) throw new Error("The photo could not be encoded");
    const name = file.name.replace(/\.[^./\\]+$/, "") || "photo";
    return new File([blob], `${name}.jpg`, { type: "image/jpeg" });
  } finally {
    bitmap.close();
  }
}
