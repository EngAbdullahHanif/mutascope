/**
 * What the live scorer accepts, checked in the browser before anything is sent (ADR 0040).
 *
 * The rules are the API's (`api.app.schemas.ProteinSequence`): only the 20 canonical amino
 * acids, uppercase, 1–1,022 residues; precise mode also at most the server's
 * `precise_max_length`, which is read from `/ready`, never assumed here. Pasted text is tidied
 * first (a FASTA header line, whitespace and line breaks removed, letters uppercased), so what
 * is checked is exactly what would be sent.
 */
import { AA_ORDER } from "@/lib/explorer/landscape";

/** ESM-2's longest input without windows (`MAX_SEQUENCE_LENGTH` in the API). */
export const MAX_LENGTH = 1022;

export type SequenceCheck = { ok: true; sequence: string } | { ok: false; message: string };

/** Drop a FASTA header and all whitespace; uppercase the rest. */
export function normalizeSequence(text: string): string {
  const lines = text.split(/\r?\n/);
  const body = lines[0]?.trimStart().startsWith(">") ? lines.slice(1) : lines;
  return body.join("").replace(/\s+/g, "").toUpperCase();
}

/** The API's own error, worded for a reader: the first bad residue, 1-indexed. */
export function checkSequence(text: string): SequenceCheck {
  const sequence = normalizeSequence(text);
  if (sequence.length === 0) return { ok: false, message: "Paste a protein sequence." };
  const bad: number[] = [];
  for (let i = 0; i < sequence.length; i++) {
    if (!AA_ORDER.includes(sequence[i]!)) bad.push(i);
  }
  if (bad.length > 0) {
    const i = bad[0]!;
    const more = bad.length > 1 ? ` (and ${bad.length - 1} more)` : "";
    return {
      ok: false,
      message: `“${sequence[i]}” at position ${i + 1} is not one of the 20 canonical amino acids (${AA_ORDER})${more}.`,
    };
  }
  if (sequence.length > MAX_LENGTH) {
    return {
      ok: false,
      message: `${sequence.length.toLocaleString("en-US")} residues: the limit is ${MAX_LENGTH.toLocaleString("en-US")} (ESM-2's longest input).`,
    };
  }
  return { ok: true, sequence };
}

/** Precise mode's extra limit, from the server's setting. */
export function preciseLengthError(length: number, limit: number): string | null {
  return length > limit
    ? `Precise mode takes at most ${limit.toLocaleString("en-US")} residues on this server (this sequence has ${length.toLocaleString("en-US")}). Use fast mode, or a shorter sequence.`
    : null;
}

export type Example = {
  name: string;
  /** The ProteinGym assay the sequence is the target of, for the Explorer link. */
  assayId: string;
  sequence: string;
};

/** Target sequences copied from the pinned bundle (`assay.target_seq` of each assay). */
export const EXAMPLES: Example[] = [
  {
    name: "Ubiquitin (yeast, 128 residues)",
    assayId: "RL40A_YEAST_Roscoe_2013",
    sequence:
      "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGGIIEPSLKALASKYNCDKSVCRKCYARLPPRATNCRKRKCGHTNQLRPKKKLK",
  },
  {
    name: "GFP (238 residues)",
    assayId: "GFP_AEQVI_Sarkisyan_2016",
    sequence:
      "MSKGEELFTGVVPILVELDGDVNGHKFSVSGEGEGDATYGKLTLKFICTTGKLPVPWPTLVTTLSYGVQCFSRYPDHMKQHDFFKSAMPEGYVQERTIFFKDDGNYKTRAEVKFEGDTLVNRIELKGIDFKEDGNILGHKLEYNYNSHNVYIMADKQKNGIKVNFKIRHNIEDGSVQLADHYQQNTPIGDGPVLLPDNHYLSTQSALSKDPNEKRDHMVLLEFVTAAGITHGMDELYK",
  },
  {
    name: "PTEN (human, 403 residues)",
    assayId: "PTEN_HUMAN_Mighell_2018",
    sequence:
      "MTAIIKEIVSRNKRRYQEDGFDLDLTYIYPNIIAMGFPAERLEGVYRNNIDDVVRFLDSKHKNHYKIYNLCAERHYDTAKFNCRVAQYPFEDHNPPQLELIKPFCEDLDQWLSEDDNHVAAIHCKAGKGRTGVMICAYLLHRGKFLKAQEALDFYGEVRTRDKKGVTIPSQRRYVYYYSYLLKNHLDYRPVALLFHKMMFETIPMFSGGTCNPQFVVCQLKVKIYSSNSGPTRREDKFMYFEFPQPLPVCGDIKVEFFHKQNKMLKKDKMFHFWVNTFFIPGPEETSEKVENGSLCDQEIDSICSIERADNDKEYLVLTLTKNDLDKANKDKANRYFSPNFKVKLYFTKTVEEPSNPEASSSTSVTPDVSDNEPDHYRYSDTTDSDPENEPFDEDQHTQITKV",
  },
];
