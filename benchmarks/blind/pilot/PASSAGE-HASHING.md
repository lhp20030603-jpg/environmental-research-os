# Pilot-8 supporting-passage hash contract

Each `supporting_passage_hash` binds a claim to the smallest sentence or
contiguous sentence fragment on the recorded PDF page that directly supports
the normalized claim. The source wording is not persisted in this repository.

The curator computes the digest from the locally held Zotero attachment as
follows:

1. inspect the page recorded by `locator.page` and the named section;
2. transcribe only the smallest directly supporting passage;
3. normalize the passage with Unicode NFKC;
4. remove soft-hyphen characters, collapse every whitespace run to one ASCII
   space, and strip leading and trailing whitespace;
5. hash the UTF-8 bytes with SHA-256.

The PDF itself is independently bound by `source_content_hash`. A verifier
must reproduce both the attachment digest and the passage digest from the
same `zotero_attachment_key` before retaining `claim_verified` status.
