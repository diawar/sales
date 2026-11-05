from django.test import TestCase

from .models import Address, Client, Contact, Evidence


class ContactScaleTests(TestCase):
    def test_large_client_with_many_emails_and_null_address(self):
        # One client with >1000 addresses also exercises nested prefetch: simply
        # batching the top-level contacts does not bound its related addresses.
        company = Client.objects.create(name='Large company', identity_key='scale-test')
        addresses = Address.objects.bulk_create([
            Address(email=f'scale-{i}@example.com') for i in range(1100)
        ])
        contacts = Contact.objects.bulk_create([
            Contact(client=company, address=address) for address in addresses
        ])
        blank = Contact.objects.create(client=company)
        Evidence.objects.create(contact=contacts[-1], source='Scale', source_url='https://example.com')
        response = self.client.get('/api/contacts')
        self.assertEqual(response.status_code, 200)
        rows = response.json()['contacts']
        self.assertEqual(len(rows), 1101)
        self.assertEqual(len(rows[0]['all_emails']), 1100)
        self.assertEqual(rows[-1]['id'], blank.id)
        self.assertEqual(rows[-1]['email'], '')
        self.assertEqual(rows[-2]['sources'], ['Scale'])
