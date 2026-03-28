// Package spaces provides shared DigitalOcean Spaces (S3-compatible) operations
// for uploading and downloading zone files.
package spaces

import (
	"fmt"
	"io"
	"log"
	"os"

	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/aws/credentials"
	"github.com/aws/aws-sdk-go/aws/session"
	"github.com/aws/aws-sdk-go/service/s3"
	"github.com/aws/aws-sdk-go/service/s3/s3manager"
)

// Client wraps the S3-compatible Spaces session.
type Client struct {
	sess   *session.Session
	s3     *s3.S3
	bucket string
}

// NewClient creates a Spaces client from environment variables.
// Required: SPACES_KEY, SPACES_SECRET
// Optional: SPACES_BUCKET (default "canyougrab-zone-files"), SPACES_REGION (default "nyc3")
func NewClient() (*Client, error) {
	key := os.Getenv("SPACES_KEY")
	secret := os.Getenv("SPACES_SECRET")
	if key == "" || secret == "" {
		return nil, fmt.Errorf("SPACES_KEY and SPACES_SECRET must be set")
	}

	bucket := os.Getenv("SPACES_BUCKET")
	if bucket == "" {
		bucket = "canyougrab-zone-files"
	}
	region := os.Getenv("SPACES_REGION")
	if region == "" {
		region = "nyc3"
	}

	endpoint := fmt.Sprintf("https://%s.digitaloceanspaces.com", region)

	sess, err := session.NewSession(&aws.Config{
		Credentials:      credentials.NewStaticCredentials(key, secret, ""),
		Endpoint:         aws.String(endpoint),
		Region:           aws.String("us-east-1"), // required by SDK but ignored by DO
		S3ForcePathStyle: aws.Bool(false),
	})
	if err != nil {
		return nil, fmt.Errorf("spaces session: %w", err)
	}

	return &Client{
		sess:   sess,
		s3:     s3.New(sess),
		bucket: bucket,
	}, nil
}

// Upload streams a local file to Spaces.
func (c *Client) Upload(localPath, key string) error {
	f, err := os.Open(localPath)
	if err != nil {
		return fmt.Errorf("open %s: %w", localPath, err)
	}
	defer f.Close()

	uploader := s3manager.NewUploader(c.sess, func(u *s3manager.Uploader) {
		u.PartSize = 64 * 1024 * 1024 // 64MB parts for large zone files
		u.Concurrency = 4
	})

	_, err = uploader.Upload(&s3manager.UploadInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
		Body:   f,
	})
	if err != nil {
		return fmt.Errorf("upload %s: %w", key, err)
	}

	// Get size for logging
	stat, _ := f.Stat()
	if stat != nil {
		log.Printf("Uploaded %s → s3://%s/%s (%.1f MB)", localPath, c.bucket, key, float64(stat.Size())/1024/1024)
	}
	return nil
}

// Download streams a Spaces object to a local file.
func (c *Client) Download(key, localPath string) error {
	f, err := os.Create(localPath)
	if err != nil {
		return fmt.Errorf("create %s: %w", localPath, err)
	}
	defer f.Close()

	downloader := s3manager.NewDownloader(c.sess, func(d *s3manager.Downloader) {
		d.PartSize = 64 * 1024 * 1024
		d.Concurrency = 4
	})

	n, err := downloader.Download(f, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		os.Remove(localPath)
		return fmt.Errorf("download %s: %w", key, err)
	}

	log.Printf("Downloaded s3://%s/%s → %s (%.1f MB)", c.bucket, key, localPath, float64(n)/1024/1024)
	return nil
}

// Exists checks if a key exists in the bucket.
func (c *Client) Exists(key string) bool {
	_, err := c.s3.HeadObject(&s3.HeadObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	return err == nil
}

// CopyKey copies an object within the same bucket (e.g., archive → latest).
func (c *Client) CopyKey(srcKey, dstKey string) error {
	_, err := c.s3.CopyObject(&s3.CopyObjectInput{
		Bucket:     aws.String(c.bucket),
		CopySource: aws.String(fmt.Sprintf("%s/%s", c.bucket, srcKey)),
		Key:        aws.String(dstKey),
	})
	if err != nil {
		return fmt.Errorf("copy %s → %s: %w", srcKey, dstKey, err)
	}
	return nil
}

// DownloadZoneFile downloads a zone file from the latest/ prefix in Spaces.
// This is the function bloom-builder and parking-scanner should call.
func (c *Client) DownloadZoneFile(tld, destPath string) error {
	key := fmt.Sprintf("latest/%s.zone.gz", tld)
	return c.Download(key, destPath)
}

// StreamZoneFile opens a zone file from Spaces and returns a ReadCloser.
// Caller must close it when done. Useful for streaming without writing to disk.
func (c *Client) StreamZoneFile(tld string) (io.ReadCloser, error) {
	key := fmt.Sprintf("latest/%s.zone.gz", tld)
	resp, err := c.s3.GetObject(&s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("get %s: %w", key, err)
	}
	return resp.Body, nil
}
