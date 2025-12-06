import sys
import os
import json

# Add the parent directory to sys.path to allow importing app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.finnhub_service import finnhub_service

def main():
    symbol = "NVDA"
    print(f"Fetching earnings call transcripts list for {symbol}...")
    
    transcript_list = finnhub_service.get_transcript_list(symbol)
    
    if not transcript_list:
        print("No transcripts found or error occurred.")
        # If list is empty, it might be due to permissions or no data
        return
        
    print(f"Found {len(transcript_list)} transcripts.")
    
    # Print the first item in the list to see structure (usually contains ID, year, quarter, time)
    if len(transcript_list) > 0:
        first_item = transcript_list[0]
        print("\n--- Latest Transcript Metadata ---")
        print(json.dumps(first_item, indent=2))
        
        transcript_id = first_item.get('id')
        if transcript_id:
            print(f"\nFetching content for Transcript ID: {transcript_id}...")
            content = finnhub_service.get_transcript_content(transcript_id)
            
            if content:
                print("\n--- Transcript Content (Snippet) ---")
                # Usually contains 'content', 'audio', 'participant', etc.
                # Let's check keys first
                print(f"Keys available: {list(content.keys())}")
                
                # Check for 'speech' or 'transcript' text
                # The structure is often a list of speech objects (name, speech)
                if 'transcript' in content:
                    print(f"\nTranscript has {len(content['transcript'])} speech segments.")
                    if len(content['transcript']) > 0:
                        print("First segment:")
                        print(json.dumps(content['transcript'][0], indent=2))
                else:
                    # Depending on API version, it might be just text or different structure
                    print("Structure inspection:")
                    print(str(content)[:500])
            else:
                print("Failed to fetch content.")

if __name__ == "__main__":
    main()
