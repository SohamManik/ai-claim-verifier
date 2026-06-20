import hashlib

class FraudCache:
    """
    A simple in-memory cache to track image hashes and detect duplicate submissions.
    In production, this would be backed by Redis or a database.
    """
    def __init__(self):
        # Maps SHA-256 hash string -> user_id who first submitted it
        self._cache: dict[str, str] = {}

    def compute_hash(self, image_bytes: bytes) -> str:
        """Compute the SHA-256 hash of raw image bytes."""
        return hashlib.sha256(image_bytes).hexdigest()

    def check_and_add(self, image_hash: str, user_id: str) -> bool:
        """
        Check if an image hash has been seen before by a DIFFERENT user.
        If not seen, adds it to the cache under the given user_id.

        Returns:
            True if the image is a known duplicate from a DIFFERENT user (Fraud).
            False if it's new, or if it was submitted by the same user (benign retry).
        """
        if image_hash in self._cache:
            original_user = self._cache[image_hash]
            if original_user != user_id:
                # Fraud! Different user submitted the exact same pixels.
                return True
            else:
                # Same user re-submitting (maybe they refreshed the page)
                return False
        else:
            # New image, record it
            self._cache[image_hash] = user_id
            return False

# Global instance for the pipeline run
global_fraud_cache = FraudCache()
