import os
import sys
import unittest
import tempfile
import sqlite3

# Add application path to system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', 'OneDrive', 'Desktop', 'Vault 716')))

try:
    import app
except ImportError:
    # Fallback to local import if run from project dir
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import app

class Vault716TestCase(unittest.TestCase):
    def setUp(self):
        # Configure app for testing
        app.app.config['TESTING'] = True
        app.app.config['WTF_CSRF_ENABLED'] = False
        
        # Setup a temporary database path
        self.db_fd, self.db_path = tempfile.mkstemp()
        app.DATABASE = self.db_path
        
        # Initialize the test client
        self.client = app.app.test_client()
        
        # Setup the database tables
        app.init_db()

    def tearDown(self):
        # Close database and remove temporary file
        self.client = None
        os.close(self.db_fd)
        try:
            os.unlink(self.db_path)
        except PermissionError:
            import gc
            gc.collect()
            try:
                os.unlink(self.db_path)
            except Exception:
                pass

    def test_database_initialization_and_seeding(self):
        """Verify the database schema contains expected tables and seed rows."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            
            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            self.assertIn('users', tables)
            self.assertIn('orders', tables)
            
            # Verify seed users
            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = cursor.fetchone()[0]
            self.assertEqual(user_count, 5) # 5 users seeded
            
            # Verify active hot count
            cursor.execute("SELECT COUNT(*) FROM users WHERE plan_tier = 'Hot' AND current_status = 'Active'")
            active_hot = cursor.fetchone()[0]
            self.assertEqual(active_hot, 2) # John and Jane are active Hot tier
        finally:
            conn.close()

    def test_homepage_render(self):
        """Verify that the landing page renders with active spots count."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'VAULT', response.data)
        self.assertIn(b'23 / 25 HOT SPOTS REMAINING', response.data) # 25 - 2 active hot users = 23 spots remaining

    def test_alternating_menu(self):
        """Verify that menu alternating logic retrieves the correct menu."""
        # Odd Week Check
        menu, week = app.get_current_menu(simulate_week=3)
        self.assertEqual(menu['name'], 'Menu A')
        self.assertEqual(menu['items'][0]['meal'], 'Chicken Tikka Masala')
        
        # Even Week Check
        menu, week = app.get_current_menu(simulate_week=4)
        self.assertEqual(menu['name'], 'Menu B')
        self.assertEqual(menu['items'][0]['meal'], 'Mediterranean Bowl')

    def test_amount_calculation(self):
        """Verify tier costs and modifiers."""
        # Hot, no upgrade = $50
        self.assertEqual(app.calculate_amount('Hot', 0), 50.00)
        # Hot, gym upgrade = $65
        self.assertEqual(app.calculate_amount('Hot', 1), 65.00)
        # Frozen, no upgrade = $55
        self.assertEqual(app.calculate_amount('Frozen', 0), 55.00)
        # Frozen, gym upgrade = $70
        self.assertEqual(app.calculate_amount('Frozen', 1), 70.00)

    def test_admin_portal_data(self):
        """Verify admin metrics calculations."""
        # Render admin-portal
        response = self.client.get('/admin-portal')
        self.assertEqual(response.status_code, 200)
        
        # Bulk chicken computation:
        # We have 4 orders seeded:
        # John (Hot, Std) -> 1.875 lbs
        # Jane (Hot, Gym) -> 2.5 lbs
        # Bob (Frozen, Std) -> 1.875 lbs
        # Alice (Frozen, Gym) -> 2.5 lbs
        # Total orders: 4. Standard: 2. Gym: 2.
        # Standard: 2 * 1.875 = 3.75 lbs
        # Gym: 2 * 2.5 = 5.0 lbs
        # Total chicken = 8.75 lbs
        self.assertIn(b'8.75', response.data)
        self.assertIn(b'2 / 25', response.data) # Active Hot Subscribers: 2 / 25
        self.assertIn(b'10', response.data) # Gym/Standard: 2 * 5 = 10 containers

    def test_admin_mark_paid(self):
        """Verify AJAX endpoint for marking order as paid."""
        # Post to mark order 2 paid (Jane Smith, order_number 1001)
        response = self.client.post('/admin/order/2/pay')
        self.assertEqual(response.status_code, 200)
        
        # Verify db status
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT payment_status FROM orders WHERE id = 2")
            status = cursor.fetchone()[0]
            self.assertEqual(status, 'Paid')
        finally:
            conn.close()

    def test_tier_details_routing(self):
        """Verify unified tier_details.html renders all 10 dishes permanently."""
        # Fresh Hot Tier details sub-page
        response_hot = self.client.get('/tier/fresh-hot')
        self.assertEqual(response_hot.status_code, 200)
        self.assertIn(b'THE DAILY FRESH-HOT VAULT', response_hot.data)
        self.assertIn(b'How It Works', response_hot.data)
        self.assertIn(b'Standard Hot Plan', response_hot.data)
        self.assertIn(b'High-Protein Gym Upgrade', response_hot.data)
        # Both rotations visible simultaneously
        self.assertIn(b'Rotation A', response_hot.data)
        self.assertIn(b'Rotation B', response_hot.data)
        self.assertIn(b'Chicken Tikka Masala', response_hot.data)
        self.assertIn(b'Mediterranean Bowl', response_hot.data)

        # Weekly Frozen Tier details sub-page
        response_frozen = self.client.get('/tier/weekly-frozen')
        self.assertEqual(response_frozen.status_code, 200)
        self.assertIn(b'THE FROZEN VAULT', response_frozen.data)
        self.assertIn(b'Standard Frozen Tier', response_frozen.data)
        self.assertIn(b'Chicken Caesar Wrap', response_frozen.data)

        # Invalid tier redirects to home
        response_invalid = self.client.get('/tier/invalid')
        self.assertEqual(response_invalid.status_code, 302)

if __name__ == '__main__':
    unittest.main()
