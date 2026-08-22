import { describe, expect, it } from 'vitest';
import { itemFromZoteroData, pickBestAttachment } from './itemMapper';

describe('itemFromZoteroData', () => {
  it('returns null for notes and attachments', () => {
    expect(itemFromZoteroData({ key: 'A', itemType: 'note' })).toBeNull();
    expect(itemFromZoteroData({ key: 'A', itemType: 'attachment' })).toBeNull();
  });

  it('maps a journal article with a DOI', () => {
    const item = itemFromZoteroData({
      key: 'ABCD1234',
      itemType: 'journalArticle',
      title: 'A Study of Things',
      creators: [
        { firstName: 'Ada', lastName: 'Lovelace' },
        { name: 'Some Institute' },
      ],
      date: '2019-03-01',
      publicationTitle: 'Journal of Studies',
      DOI: 'https://doi.org/10.1234/abcd',
      dateAdded: '2020-01-01T00:00:00Z',
    });

    expect(item).toEqual({
      itemKey: 'ABCD1234',
      itemType: 'journalArticle',
      title: 'A Study of Things',
      creators: ['Ada Lovelace', 'Some Institute'],
      year: 2019,
      venue: 'Journal of Studies',
      doi: '10.1234/abcd',
      dateAdded: '2020-01-01T00:00:00Z',
      hasAttachment: false,
    });
  });

  it('falls back to websiteTitle when there is no publicationTitle', () => {
    const item = itemFromZoteroData({
      key: 'X',
      itemType: 'webpage',
      websiteTitle: 'Some Blog',
    });
    expect(item?.venue).toBe('Some Blog');
  });

  it('defaults title to "(untitled)" and leaves year/doi/venue null when absent', () => {
    const item = itemFromZoteroData({ key: 'X', itemType: 'book' });
    expect(item).toEqual({
      itemKey: 'X',
      itemType: 'book',
      title: '(untitled)',
      creators: [],
      year: null,
      venue: null,
      doi: null,
      dateAdded: null,
      hasAttachment: false,
    });
  });

  it('strips a bare doi: prefix', () => {
    const item = itemFromZoteroData({ key: 'X', itemType: 'book', DOI: 'doi:10.5/xyz' });
    expect(item?.doi).toBe('10.5/xyz');
  });

  it('extracts a 4-digit year from various date formats', () => {
    expect(itemFromZoteroData({ key: 'X', itemType: 'book', date: '2021' })?.year).toBe(2021);
    expect(itemFromZoteroData({ key: 'X', itemType: 'book', date: 'March 2018' })?.year).toBe(2018);
    expect(itemFromZoteroData({ key: 'X', itemType: 'book', date: 'n.d.' })?.year).toBeNull();
  });
});

describe('pickBestAttachment', () => {
  it('prefers a downloadable PDF over an HTML snapshot', () => {
    const best = pickBestAttachment([
      { itemKey: 'HTML1', contentType: 'text/html', linkMode: 'imported_url' },
      { itemKey: 'PDF1', contentType: 'application/pdf', linkMode: 'imported_file' },
    ]);
    expect(best?.itemKey).toBe('PDF1');
  });

  it('falls back to a downloadable HTML snapshot when no PDF exists', () => {
    const best = pickBestAttachment([
      { itemKey: 'HTML1', contentType: 'text/html', linkMode: 'imported_url' },
    ]);
    expect(best?.itemKey).toBe('HTML1');
  });

  it('skips linked_file/linked_url attachments even if a PDF', () => {
    const best = pickBestAttachment([
      { itemKey: 'LINKED', contentType: 'application/pdf', linkMode: 'linked_file' },
    ]);
    expect(best).toBeNull();
  });

  it('returns null when there is nothing downloadable', () => {
    expect(pickBestAttachment([])).toBeNull();
  });
});
