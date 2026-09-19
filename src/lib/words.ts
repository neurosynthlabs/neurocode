/** "1 file", "2 files": a count with the noun that agrees with it. */
export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
